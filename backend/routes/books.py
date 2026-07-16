import os
import uuid
import requests
import urllib.parse
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from concurrent.futures import ThreadPoolExecutor
import difflib

from backend.config import supabase, chroma_client
from backend.auth import get_current_user
from backend.models import LinkCatalogueBookRequest
from backend.db_helpers import (
    save_book_url,
    get_book_url,
    resolve_ia_pdf_url
)
from backend.llm import is_pdf_valid
from backend.tasks import index_catalogue_book_task

router = APIRouter(tags=["books"])

@router.get("/global-books")
def list_global_books():
    try:
        res = supabase.table("global_books").select("*").execute()
        return res.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

import time

# In-memory caches to speed up catalog queries
_OPENSTAX_CATALOG_CACHE = None
_OPENSTAX_CACHE_TIME = 0.0
_SEARCH_QUERY_CACHE = {}

def get_openstax_all_books():
    global _OPENSTAX_CATALOG_CACHE, _OPENSTAX_CACHE_TIME
    now = time.time()
    if _OPENSTAX_CATALOG_CACHE is not None and (now - _OPENSTAX_CACHE_TIME < 86400):  # 24h cache
        return _OPENSTAX_CATALOG_CACHE
        
    try:
        url = "https://openstax.org/apps/cms/api/v2/pages/?type=books.Book&limit=250"
        res = requests.get(url, timeout=10).json()
        matched_items = res.get("items", [])
        
        def fetch_detail(item):
            try:
                detail = requests.get(item["meta"]["detail_url"], timeout=3).json()
                return {
                    "source_id": str(item["id"]),
                    "title": item["title"],
                    "pdf_url": detail.get("high_resolution_pdf_url"),
                    "cover_url": detail.get("cover_url"),
                    "description": detail.get("description", ""),
                    "author": "OpenStax",
                    "source": "openstax"
                }
            except Exception:
                return None
                
        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(fetch_detail, matched_items))
            
        valid_results = [r for r in results if r is not None]
        if valid_results:
            _OPENSTAX_CATALOG_CACHE = valid_results
            _OPENSTAX_CACHE_TIME = now
            return valid_results
    except Exception as e:
        print(f"Error building OpenStax cache: {e}")
        if _OPENSTAX_CATALOG_CACHE is not None:
            return _OPENSTAX_CATALOG_CACHE
    return []

@router.get("/catalogue/search")
def search_catalogue(query: str = ""):
    query_clean = query.strip().lower()
    now = time.time()
    
    if query_clean in _SEARCH_QUERY_CACHE:
        cached_res, timestamp = _SEARCH_QUERY_CACHE[query_clean]
        if now - timestamp < 600:  # 10 minutes cache
            return cached_res

    q_lower = query_clean
    q_words = set(q_lower.split()) if q_lower else set()

    def relevance_score(book: dict) -> float:
        title = book.get("title", "").lower()
        desc = book.get("description", "").lower()
        if not q_lower:
            return 0.0

        # Exact match
        if q_lower == title:
            return 1.0
        # Title starts with query — very strong signal
        if title.startswith(q_lower):
            return 0.95 - (len(title) - len(q_lower)) * 0.001
        # Query appears fully in title
        if q_lower in title:
            score = 0.82 + (len(q_lower) / max(len(title), 1)) * 0.12
        else:
            score = 0.0

        t_words = set(title.split())
        d_words = set(desc.split())

        # Word overlap on title — high weight
        if q_words:
            title_overlap = len(q_words & t_words) / len(q_words)
            if title_overlap > 0:
                score = max(score, 0.5 + title_overlap * 0.42)

            # Bigram overlap (catches partial word matches like "calc" in "calculus")
            for qw in q_words:
                for tw in t_words:
                    if len(qw) >= 4 and tw.startswith(qw):
                        score = max(score, 0.45)
                        break

            desc_overlap = len(q_words & d_words) / len(q_words)
            if desc_overlap > 0:
                score = max(score, 0.15 + desc_overlap * 0.18)

        # Fuzzy fallback — penalise long titles to prefer tight matches
        seq_ratio = difflib.SequenceMatcher(None, q_lower, title).ratio()
        brevity_bonus = max(0.0, 1.0 - len(title) / 120) * 0.05
        score = max(score, seq_ratio * 0.38 + brevity_bonus)
        return score

    def fetch_openstax():
        books = get_openstax_all_books()
        if not q_lower:
            return books[:12]
        matched = []
        for b in books:
            t = b["title"].lower()
            d = (b.get("description") or "").lower()
            if q_lower in t or any(w in t for w in q_words) or q_lower in d:
                matched.append(b)
        matched.sort(key=relevance_score, reverse=True)
        return matched[:12]

    def fetch_gutenberg():
        if not q_lower:
            return []
        try:
            url = f"https://gutendex.com/books/?search={urllib.parse.quote(query)}&mime_type=application/pdf"
            res = requests.get(url, timeout=5).json()  # tight 5s timeout
            results = []
            for item in res.get("results", [])[:6]:
                formats = item.get("formats", {})
                pdf_url = None
                for ftype in ["application/pdf", "text/plain; charset=utf-8", "text/plain"]:
                    for k, v in formats.items():
                        if ftype in k:
                            pdf_url = v
                            break
                    if pdf_url:
                        break
                if not pdf_url:
                    continue

                author_names = [a.get("name") for a in item.get("authors", [])]
                author_str = ", ".join(author_names) if author_names else "Project Gutenberg"

                results.append({
                    "source_id": str(item["id"]),
                    "title": item["title"],
                    "pdf_url": pdf_url,
                    "cover_url": formats.get("image/jpeg", ""),
                    "description": "Project Gutenberg public domain text",
                    "author": author_str,
                    "source": "gutenberg"
                })
            return results
        except Exception as e:
            print(f"Gutenberg search failed: {e}")
            return []

    def fetch_libretexts():
        if not q_lower:
            return []
        try:
            url = f"https://commons.libretexts.org/api/v1/commons/catalog?search={urllib.parse.quote(query)}"
            res = requests.get(url, timeout=5, headers={"Accept": "application/json"}).json()  # tight 5s
            results = []
            for item in res.get("books", [])[:8]:
                pdf_link = item.get("links", {}).get("pdf")
                if not pdf_link:
                    continue
                results.append({
                    "source_id": item.get("bookID", ""),
                    "title": item.get("title", "LibreTexts Book"),
                    "pdf_url": pdf_link,
                    "cover_url": item.get("thumbnail", ""),
                    "description": item.get("course", "") or "LibreTexts Open Educational Resource",
                    "author": item.get("author") or "LibreTexts",
                    "source": "libretexts"
                })
            return results
        except Exception as e:
            print(f"LibreTexts search failed: {e}")
            return []

    def fetch_otl():
        if not q_lower:
            return []
        try:
            url = f"https://open.umn.edu/opentextbooks/textbooks.json?q={urllib.parse.quote(query)}"
            res = requests.get(url, timeout=5).json()  # tight 5s timeout
            results = []
            for item in res.get("data", [])[:8]:
                formats = item.get("formats", [])
                pdf_url = None
                for fmt in formats:
                    if fmt.get("type", "").upper() == "PDF":
                        pdf_url = fmt.get("url")
                        break
                if not pdf_url:
                    pdf_url = item.get("url")
                if not pdf_url:
                    continue

                desc = item.get("description", "") or ""
                desc_clean = re.sub(r"<[^>]+>", "", desc).strip()

                contribs = item.get("contributors", [])
                authors = []
                for c in contribs:
                    name_parts = [c.get("first_name"), c.get("middle_name"), c.get("last_name")]
                    full_name = " ".join([p for p in name_parts if p])
                    if full_name:
                        authors.append(full_name)
                author_str = ", ".join(authors) if authors else "Open Textbook Library"

                results.append({
                    "source_id": str(item.get("id")),
                    "title": item.get("title", ""),
                    "pdf_url": pdf_url,
                    "cover_url": item.get("cover", {}).get("url", "") if isinstance(item.get("cover"), dict) else "",
                    "description": desc_clean[:250] + "...",
                    "author": author_str,
                    "source": "opentextbooklibrary"
                })
            return results
        except Exception as e:
            print(f"Open Textbook Library search failed: {e}")
            return []

    def fetch_doab():
        if not q_lower:
            return []
        try:
            url = f"https://directory.doabooks.org/rest/search?query={urllib.parse.quote(query)}&expand=metadata&limit=8&offset=0"
            res = requests.get(url, timeout=6, headers={"Accept": "application/json"}).json()  # tight 6s timeout
            results = []
            for item in res[:8]:
                meta_list = item.get("metadata", [])
                def get_meta(key):
                    for m in meta_list:
                        if m.get("key") == key:
                            return m.get("value", "")
                    return ""

                title_text = get_meta("dc.title") or item.get("name", "DOAB Book")
                author = get_meta("dc.contributor.author") or get_meta("dc.creator") or "DOAB"
                desc_raw = get_meta("dc.description.abstract") or get_meta("dc.description") or "Open Access book"
                desc_clean = re.sub(r"<[^>]+>", "", desc_raw).strip()

                handle = item.get("handle", "")
                if handle:
                    pdf_url = f"https://directory.doabooks.org/handle/{handle}"
                elif item.get("link"):
                    pdf_url = item["link"]
                else:
                    continue

                results.append({
                    "source_id": str(item.get("id", hash(pdf_url))),
                    "title": title_text,
                    "pdf_url": pdf_url,
                    "cover_url": "",
                    "description": desc_clean[:250] + "...",
                    "author": author,
                    "source": "doab"
                })
            return results
        except Exception as e:
            print(f"DOAB search failed: {e}")
            return []

    try:
        with ThreadPoolExecutor(max_workers=5) as outer_executor:
            fut_os  = outer_executor.submit(fetch_openstax)
            fut_lt  = outer_executor.submit(fetch_libretexts)
            fut_otl = outer_executor.submit(fetch_otl)
            fut_gut = outer_executor.submit(fetch_gutenberg)
            fut_doab= outer_executor.submit(fetch_doab)

            # Collect OpenStax first — fastest since it uses an in-memory cache
            os_results = fut_os.result()

            # If OpenStax already has strong hits, skip slow sources for speed
            top_scores = [relevance_score(b) for b in os_results[:5]]
            skip_slow = len(top_scores) >= 3 and sum(top_scores) / len(top_scores) >= 0.65

            if skip_slow:
                all_raw = os_results
                # Still wait for LibreTexts since it's academic and usually fast
                try:
                    all_raw += fut_lt.result(timeout=5)
                except Exception:
                    pass
            else:
                all_raw = os_results
                for fut in [fut_lt, fut_otl, fut_gut, fut_doab]:
                    try:
                        all_raw += fut.result(timeout=6)
                    except Exception:
                        pass

        seen_titles = set()
        deduped = []
        for b in all_raw:
            t = b.get("title", "").lower().strip()
            if t not in seen_titles:
                seen_titles.add(t)
                deduped.append(b)

        deduped.sort(key=relevance_score, reverse=True)
        final = deduped[:20]  # Return top-20 for better coverage
        _SEARCH_QUERY_CACHE[query_clean] = (final, now)
        return final
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch global catalogue: {str(e)}")


@router.post("/subjects/{subject_id}/books/global")
def link_catalogue_book(
    subject_id: str,
    req: LinkCatalogueBookRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user)
):
    subject = supabase.table("subjects").select("*").eq("id", subject_id).eq("user_id", user_id).execute()
    if not subject.data:
        raise HTTPException(status_code=404, detail="Subject not found or access denied")
        
    existing_book = supabase.table("global_books").select("*").eq("title", req.title).execute()
    
    if existing_book.data:
        book_id = existing_book.data[0]["id"]
        collection_name = existing_book.data[0]["chroma_collection_name"]
        local_pdf = f"books/{book_id}.pdf"
        local_txt = f"books/{book_id}.txt"
        if not os.path.exists(local_pdf) and not os.path.exists(local_txt):
            background_tasks.add_task(
                index_catalogue_book_task,
                book_id,
                req.pdf_url,
                req.title,
                collection_name
            )
    else:
        book_id = str(uuid.uuid4())
        collection_name = f"book_{uuid.uuid4().hex}"
        
        try:
            supabase.table("global_books").insert({
                "id": book_id,
                "title": req.title,
                "source": req.source,
                "chroma_collection_name": collection_name
            }).execute()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to register global book: {str(e)}")
            
        background_tasks.add_task(
            index_catalogue_book_task,
            book_id,
            req.pdf_url,
            req.title,
            collection_name
        )
        
    save_book_url(book_id, req.pdf_url)
        
    linked = supabase.table("subject_books").select("*").eq("subject_id", subject_id).eq("global_book_id", book_id).execute()
    if not linked.data:
        try:
            supabase.table("subject_books").insert({
                "subject_id": subject_id,
                "global_book_id": book_id
            }).execute()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to link book to subject: {str(e)}")
            
    return {"message": "Book linked successfully", "global_book_id": book_id}


@router.post("/subjects/{subject_id}/books/{global_book_id}")
def link_book_to_subject(subject_id: str, global_book_id: str, user_id: str = Depends(get_current_user)):
    subject = supabase.table("subjects").select("*").eq("id", subject_id).eq("user_id", user_id).execute()
    if not subject.data:
        raise HTTPException(status_code=404, detail="Subject not found")

    book = supabase.table("global_books").select("*").eq("id", global_book_id).execute()
    if not book.data:
        raise HTTPException(status_code=404, detail="Book not found")

    result = supabase.table("subject_books").insert({
        "subject_id": subject_id,
        "global_book_id": global_book_id,
    }).execute()

    return {"message": "Book linked", "data": result.data}


@router.delete("/subjects/{subject_id}/books/{global_book_id}")
def unlink_book_from_subject(subject_id: str, global_book_id: str, user_id: str = Depends(get_current_user)):
    subject = supabase.table("subjects").select("*").eq("id", subject_id).eq("user_id", user_id).execute()
    if not subject.data:
        raise HTTPException(status_code=404, detail="Subject not found or access denied")
        
    try:
        supabase.table("subject_books").delete().eq("subject_id", subject_id).eq("global_book_id", global_book_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to unlink book: {str(e)}")
        
    return {"message": "Book unlinked successfully"}


@router.get("/subjects/{subject_id}/books/{global_book_id}/url")
def get_book_url_endpoint(
    subject_id: str,
    global_book_id: str,
    user_id: str = Depends(get_current_user)
):
    subject = supabase.table("subjects").select("id").eq("id", subject_id).eq("user_id", user_id).execute()
    if not subject.data:
        raise HTTPException(status_code=404, detail="Subject not found or access denied")
        
    linked = supabase.table("subject_books").select("*").eq("subject_id", subject_id).eq("global_book_id", global_book_id).execute()
    if not linked.data:
        raise HTTPException(status_code=404, detail="Book not linked to this subject")
        
    pdf_url = get_book_url(global_book_id)
    if not pdf_url:
        book = supabase.table("global_books").select("*").eq("id", global_book_id).execute()
        if book.data:
            title = book.data[0]["title"]
            source = book.data[0].get("source", "openstax")
            try:
                if source == "openstax":
                    api_res = requests.get("https://openstax.org/apps/cms/api/v2/pages/?type=books.Book&limit=250", timeout=10).json()
                    for item in api_res.get("items", []):
                        item_title = item.get("title", "").lower()
                        if title.lower() in item_title or item_title in title.lower():
                            detail = requests.get(item["meta"]["detail_url"], timeout=10).json()
                            pdf_url = detail.get("high_resolution_pdf_url")
                            if pdf_url:
                                save_book_url(global_book_id, pdf_url)
                                break
                else:
                    ia_res = requests.get(
                        f"https://archive.org/advancedsearch.php?q={urllib.parse.quote(title)}+AND+mediatype%3Atexts+AND+%28format%3Apdf+OR+format%3A%22Text+PDF%22%29&fl[]=identifier&fl[]=title&rows=5&output=json",
                        timeout=10
                    ).json()
                    for doc in ia_res.get("response", {}).get("docs", []):
                        ident = doc.get("identifier", "")
                        if not ident:
                            continue
                        resolved = resolve_ia_pdf_url(ident)
                        if resolved:
                            pdf_url = resolved
                            save_book_url(global_book_id, pdf_url)
                            break
            except Exception as e:
                print(f"Dynamic catalog lookup failed in url endpoint: {e}")

    if pdf_url and "archive.org/details/" in pdf_url:
        ident = pdf_url.split("archive.org/details/")[-1].strip("/")
        resolved = resolve_ia_pdf_url(ident)
        if resolved:
            pdf_url = resolved
            save_book_url(global_book_id, pdf_url)

    if not pdf_url:
        raise HTTPException(status_code=404, detail="Book PDF download URL could not be resolved.")

    if "drive.google.com" in pdf_url and "/file/d/" in pdf_url:
        parts = pdf_url.split("/file/d/")
        if len(parts) > 1:
            file_id = parts[1].split("/")[0].split("?")[0]
            pdf_url = f"https://drive.google.com/uc?export=download&id={file_id}"
            save_book_url(global_book_id, pdf_url)

    return {"url": pdf_url, "local": False, "is_pdf": True}


@router.get("/subjects/{subject_id}/books/{global_book_id}/file")
def get_book_file(
    subject_id: str,
    global_book_id: str,
    user_id: str = Depends(get_current_user)
):
    subject = supabase.table("subjects").select("id").eq("id", subject_id).eq("user_id", user_id).execute()
    if not subject.data:
        raise HTTPException(status_code=404, detail="Subject not found or access denied")
        
    linked = supabase.table("subject_books").select("*").eq("subject_id", subject_id).eq("global_book_id", global_book_id).execute()
    if not linked.data:
        raise HTTPException(status_code=404, detail="Book not linked to this subject")
        
    pdf_path = f"books/{global_book_id}.pdf"
    txt_path = f"books/{global_book_id}.txt"
    
    if os.path.exists(txt_path) and is_pdf_valid(txt_path):
        return FileResponse(
            txt_path,
            media_type="text/plain",
            headers={"Cache-Control": "public, max-age=86400"}
        )
    if is_pdf_valid(pdf_path):
        return FileResponse(
            pdf_path,
            media_type="application/pdf",
            headers={"Cache-Control": "public, max-age=86400"}
        )
    
    pdf_url = get_book_url(global_book_id)
    if not pdf_url:
        book = supabase.table("global_books").select("*").eq("id", global_book_id).execute()
        if not book.data:
            raise HTTPException(status_code=404, detail="Book not found in database")
        
        title = book.data[0]["title"]
        source = book.data[0].get("source", "openstax")
        
        try:
            if source == "openstax":
                api_res = requests.get("https://openstax.org/apps/cms/api/v2/pages/?type=books.Book&limit=250", timeout=10).json()
                for item in api_res.get("items", []):
                    item_title = item.get("title", "").lower()
                    if title.lower() in item_title or item_title in title.lower():
                        detail = requests.get(item["meta"]["detail_url"], timeout=10).json()
                        pdf_url = detail.get("high_resolution_pdf_url")
                        break
            elif source == "gutenberg":
                api_res = requests.get(f"https://gutendex.com/books/?search={urllib.parse.quote(title)}", timeout=10).json()
                for item in api_res.get("results", []):
                    formats = item.get("formats", {})
                    for ftype, furl in formats.items():
                        if "text/plain" in ftype:
                            pdf_url = furl
                            break
                    if not pdf_url:
                        for ftype, furl in formats.items():
                            if "text/html" in ftype:
                                pdf_url = furl
                                break
                    if pdf_url:
                        break
            elif source == "libretexts":
                api_res = requests.get(f"https://commons.libretexts.org/api/v1/commons/catalog?search={urllib.parse.quote(title)}", timeout=10, headers={'Accept': 'application/json'}).json()
                for item in api_res.get("books", []):
                    pdf_link = item.get("links", {}).get("pdf")
                    if pdf_link:
                        pdf_url = pdf_link
                        break
            elif source == "opentextbooklibrary":
                try:
                    api_res = requests.get(f"https://open.umn.edu/opentextbooks/textbooks/{global_book_id}.json", timeout=10).json()
                    detail = api_res.get("data", {})
                    formats = detail.get("formats", [])
                    for fmt in formats:
                        if fmt.get("type", "").upper() == "PDF":
                            pdf_url = fmt.get("url")
                            break
                    if not pdf_url and formats:
                        pdf_url = formats[0].get("url")
                    if not pdf_url:
                        pdf_url = detail.get("url")
                except Exception as e:
                    print(f"OTL detail lookup failed: {e}")
                        
            if pdf_url:
                save_book_url(global_book_id, pdf_url)
        except Exception as e:
            print(f"Dynamic catalog lookup failed: {e}")
            
    if pdf_url and "archive.org/details/" in pdf_url:
        ident = pdf_url.split("archive.org/details/")[-1].strip("/")
        resolved = resolve_ia_pdf_url(ident)
        if resolved:
            pdf_url = resolved
            save_book_url(global_book_id, pdf_url)
            
    if not pdf_url:
        raise HTTPException(status_code=404, detail="Book PDF download URL could not be resolved.")

    if "drive.google.com" in pdf_url and "/file/d/" in pdf_url:
        parts = pdf_url.split("/file/d/")
        if len(parts) > 1:
            file_id = parts[1].split("/")[0].split("?")[0]
            pdf_url = f"https://drive.google.com/uc?export=download&id={file_id}"
        
    try:
        os.makedirs("books", exist_ok=True)
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/120.0.0.0",
            "Accept": "application/pdf,text/plain,*/*"
        }
        res = requests.get(pdf_url, stream=True, headers=headers, timeout=60, allow_redirects=True)
        res.raise_for_status()
        
        content_type = res.headers.get("content-type", "").lower()
        is_text = "text/plain" in content_type or pdf_url.endswith(".txt") or ".txt." in pdf_url
        file_ext = ".txt" if is_text else ".pdf"
        file_path = f"books/{global_book_id}{file_ext}"
        
        if "text/html" in content_type and not is_text:
            raise HTTPException(
                status_code=422,
                detail=f"Book URL resolved to a webpage, not a valid book file."
            )
        
        with open(file_path, "wb") as f:
            for chunk in res.iter_content(chunk_size=8192):
                f.write(chunk)
    except HTTPException:
        raise
    except Exception as download_err:
        for p in [pdf_path, txt_path]:
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        raise HTTPException(status_code=500, detail=f"Failed to download book file: {str(download_err)}")
        
    if not is_pdf_valid(file_path):
        try:
            os.remove(file_path)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="Downloaded file is not valid.")
        
    if file_path.endswith(".txt"):
        return FileResponse(
            file_path,
            media_type="text/plain",
            headers={"Cache-Control": "public, max-age=86400"}
        )
        
    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        headers={"Cache-Control": "public, max-age=86400"}
    )
