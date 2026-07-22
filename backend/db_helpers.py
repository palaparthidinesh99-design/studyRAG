import re
import time
import json
import os
import requests
import urllib.parse
from typing import Optional, List, Dict
from fastapi import HTTPException
from backend.config import supabase, chroma_client

# English stop words for NLP keyword extraction
_STOP_WORDS = {
    "a", "an", "the", "is", "it", "its", "in", "on", "at", "to", "of", "for", "and",
    "or", "but", "with", "from", "by", "as", "do", "does", "did", "be", "been", "being",
    "was", "were", "are", "have", "has", "had", "will", "would", "can", "could",
    "should", "may", "might", "must", "shall", "that", "this", "these", "those",
    "what", "which", "who", "whom", "when", "where", "why", "how", "if", "so", "then",
    "also", "just", "very", "more", "most", "much", "many", "some", "any", "no", "not",
    "you", "your", "we", "our", "they", "their", "he", "she", "his", "her", "me", "my",
    "i", "about", "into", "through", "up", "down", "out", "off", "over", "under",
    "again", "further", "then", "once", "here", "there", "all", "both", "each",
    "few", "own", "same", "than", "too", "only", "while", "tell", "me", "please",
    "show", "find", "search", "give", "get", "make", "use", "used", "using",
    "explain", "describe", "define", "summarize", "discuss", "say",
    "question", "answer", "topic", "concept", "information", "related",
}

# Conversational prefixes to strip before keyword extraction
_CONV_PREFIXES = [
    "what is", "what are", "what does", "what do",
    "how to", "how do", "how does", "how can", "how is",
    "can you", "could you", "please", "tell me about", "explain",
    "describe", "give me", "show me", "find", "search for",
    "i want to know", "i want to understand", "in the book",
    "in my notes", "from the notes", "about", "defined as",
    "meaning of", "definition of",
]

def clean_search_query(query: str) -> str:
    q = query.strip()
    q_lower = q.lower()

    for prefix in sorted(_CONV_PREFIXES, key=len, reverse=True):
        if q_lower.startswith(prefix):
            q = q[len(prefix):].strip()
            q_lower = q.lower()
            break

    tokens = re.findall(r'[A-Za-z0-9_+#<>/]+', q)
    keywords = [
        t for t in tokens
        if t.lower() not in _STOP_WORDS and len(t) >= 2
    ]

    if not keywords:
        return query

    return " ".join(keywords)

def rank_and_filter_resources(query_text: str, collections_list: list) -> list:
    keywords = set(clean_search_query(query_text).lower().split())
    if not keywords or len(collections_list) <= 1:
        return collections_list[:2]

    scored = []
    for col in collections_list:
        name_words = set(re.findall(r'\w+', col["source_name"].lower()))
        overlap = keywords.intersection(name_words)
        scored.append((len(overlap), col))

    scored.sort(key=lambda x: x[0], reverse=True)

    if scored[0][0] > 0:
        selected = [item[1] for item in scored if item[0] > 0]
        if len(selected) < 2 and len(collections_list) > 1:
            for item in scored:
                if item[1] not in selected:
                    selected.append(item[1])
                if len(selected) >= 2:
                    break
        return selected[:2]

    return collections_list[:2]

_CHROMA_COLLECTIONS_CACHE = {}

def get_cached_collection(name: str):
    if len(_CHROMA_COLLECTIONS_CACHE) > 20:
        _CHROMA_COLLECTIONS_CACHE.clear()
        import gc
        gc.collect()
        
    if name not in _CHROMA_COLLECTIONS_CACHE:
        try:
            col = chroma_client.get_collection(name=name)
            _CHROMA_COLLECTIONS_CACHE[name] = col
        except Exception:
            col = chroma_client.get_or_create_collection(name=name)
            _CHROMA_COLLECTIONS_CACHE[name] = col
    return _CHROMA_COLLECTIONS_CACHE[name]

_IN_MEMORY_SUBJECTS = {}
_IN_MEMORY_SUBJECT_BOOKS = {} # subject_id -> list of book dicts
_SUBJECT_CACHE = {}
_CACHE_TTL = 120  # 2 minutes TTL

def get_subject_cached_metadata(subject_id: str, user_id: str) -> dict:
    now = time.time()
    cached = _SUBJECT_CACHE.get(subject_id)
    if cached and (now - cached["timestamp"]) < _CACHE_TTL:
        return cached

    collection_name = None
    if subject_id in _IN_MEMORY_SUBJECTS:
        collection_name = _IN_MEMORY_SUBJECTS[subject_id]["chroma_collection_name"]
    else:
        try:
            subject = supabase.table("subjects").select("*").eq("id", subject_id).execute()
            if subject.data:
                collection_name = subject.data[0]["chroma_collection_name"]
        except Exception as e:
            print(f"Supabase subject metadata query notice: {e}")
            
    if not collection_name:
        collection_name = f"subject_{subject_id.replace('-', '')}"
    
    ai_note_ids = set()
    try:
        notes_res = supabase.table("sources").select("id").eq("subject_id", subject_id).in_("source_type", ["generated_note", "saved_note"]).execute()
        if notes_res.data:
            ai_note_ids = {n["id"] for n in notes_res.data}
    except Exception as e:
        print(f"Failed to fetch AI notes: {e}")

    linked_books_data = []
    try:
        linked_books = supabase.table("subject_books").select("global_book_id").eq("subject_id", subject_id).execute()
        if linked_books.data:
            book_ids = [lb["global_book_id"] for lb in linked_books.data]
            books = supabase.table("global_books").select("id", "title", "chroma_collection_name").in_("id", book_ids).execute()
            linked_books_data = books.data if books.data else []
    except Exception as e:
        print(f"Failed to fetch linked books from DB: {e}")

    mem_books = _IN_MEMORY_SUBJECT_BOOKS.get(subject_id, [])
    seen_bids = {b["id"] for b in linked_books_data}
    for mb in mem_books:
        if mb["id"] not in seen_bids:
            linked_books_data.append(mb)

    metadata = {
        "timestamp": now,
        "collection_name": collection_name,
        "ai_note_ids": ai_note_ids,
        "linked_books": linked_books_data
    }
    _SUBJECT_CACHE[subject_id] = metadata
    return metadata

def retrieve_merged_context(subject_id: str, query_text: str, user_id: str, n_results: int = 8, source_filter: str = "all"):
    meta_cache = get_subject_cached_metadata(subject_id, user_id)
    collection_name = meta_cache["collection_name"]
    ai_note_ids = meta_cache["ai_note_ids"]
    linked_books_data = meta_cache["linked_books"]
    
    is_specific_resource = False
    specific_book_info = None
    specific_note_info = None
    
    if source_filter not in ["all", "books", "notes"] and len(source_filter) == 36:
        for b in linked_books_data:
            if b["id"] == source_filter:
                is_specific_resource = True
                specific_book_info = b
                break
        
        if not is_specific_resource:
            try:
                gb_res = supabase.table("global_books").select("id", "title", "chroma_collection_name").eq("id", source_filter).execute()
                if gb_res.data:
                    is_specific_resource = True
                    specific_book_info = gb_res.data[0]
            except Exception as e:
                print(f"Error checking global_books for filter {source_filter}: {e}")

        if not is_specific_resource:
            try:
                note_check = supabase.table("sources").select("*").eq("id", source_filter).execute()
                if note_check.data:
                    is_specific_resource = True
                    specific_note_info = note_check.data[0]
            except Exception as e:
                print(f"Error checking sources for filter {source_filter}: {e}")
                
    collections_to_query = []
    
    if is_specific_resource:
        if specific_book_info:
            collections_to_query.append({
                "name": specific_book_info["chroma_collection_name"],
                "type": "global_book",
                "source_name": specific_book_info["title"],
                "book_id": specific_book_info["id"],
                "specific_source_id": None
            })
        elif specific_note_info:
            collections_to_query.append({
                "name": collection_name,
                "type": "personal",
                "source_name": specific_note_info["title"] or "Personal Note",
                "book_id": None,
                "specific_source_id": specific_note_info["id"]
            })
    else:
        if source_filter in ["all", "notes"]:
            collections_to_query.append({
                "name": collection_name,
                "type": "personal",
                "source_name": "Personal Note",
                "book_id": None,
                "specific_source_id": None
            })
        
        if source_filter in ["all", "books"]:
            for b in linked_books_data:
                collections_to_query.append({
                    "name": b["chroma_collection_name"],
                    "type": "global_book",
                    "source_name": b["title"],
                    "book_id": b["id"],
                    "specific_source_id": None
                })

    # Fallback guard: if collections_to_query is empty, default to querying all subject resources
    if not collections_to_query:
        collections_to_query.append({
            "name": collection_name,
            "type": "personal",
            "source_name": "Personal Note",
            "book_id": None,
            "specific_source_id": None
        })
        for b in linked_books_data:
            collections_to_query.append({
                "name": b["chroma_collection_name"],
                "type": "global_book",
                "source_name": b["title"],
                "book_id": b["id"],
                "specific_source_id": None
            })
        
        pass
            
    from concurrent.futures import ThreadPoolExecutor
    from backend.llm import call_gemini_embeddings

    all_chunks = []
    queries_list = [query_text]
    
    # Pre-compute query embeddings once using Gemini to save time and API calls
    query_embeddings = call_gemini_embeddings(queries_list)
    
    def query_single_collection(col_info):
        chunks = []
        try:
            col = get_cached_collection(col_info["name"])
            query_limit = n_results * 2 if col_info["type"] == "personal" else n_results
            
            where_filter = None
            if col_info.get("specific_source_id"):
                where_filter = {"source_id": col_info["specific_source_id"]}
                
            # Perform vector query using the precomputed Gemini embeddings if available
            if query_embeddings:
                results = col.query(query_embeddings=query_embeddings, n_results=query_limit, where=where_filter)
            else:
                results = col.query(query_texts=queries_list, n_results=query_limit, where=where_filter)
            
            if results and results.get("documents"):
                seen_chunk_texts = set()
                for query_idx in range(len(results["documents"])):
                    docs = results["documents"][query_idx]
                    metas = results["metadatas"][query_idx] if results.get("metadatas") else [None] * len(docs)
                    dists = results["distances"][query_idx] if results.get("distances") else [0.0] * len(docs)
                    
                    for doc, meta, dist in zip(docs, metas, dists):
                        if not doc:
                            continue
                        doc_clean = doc.strip()
                        if doc_clean in seen_chunk_texts:
                            continue
                        seen_chunk_texts.add(doc_clean)
                        
                        if meta and meta.get("source_id") in ai_note_ids:
                            continue
                            
                        # Exact keyword re-ranking boost: if the query string or key terms appear in doc or section title, boost relevance rank
                        adj_dist = dist
                        q_lower = query_text.lower().strip()
                        doc_lower = doc_clean.lower()
                        sec_title_lower = (meta.get("section_title") or "").lower() if meta else ""

                        if q_lower and (q_lower in doc_lower or q_lower in sec_title_lower):
                            adj_dist = max(0.01, dist * 0.5)
                        elif any(w in doc_lower or w in sec_title_lower for w in q_lower.split() if len(w) > 3):
                            adj_dist = max(0.05, dist * 0.8)

                        chunks.append({
                            "document": doc,
                            "metadata": meta,
                            "distance": adj_dist,
                            "raw_distance": dist,
                            "source_type": col_info["type"],
                            "source_name": col_info["source_name"],
                            "book_id": col_info.get("book_id")
                        })
        except Exception as e:
            print(f"Error querying Chroma collection {col_info['name']}: {e}")
    if collections_to_query:
        with ThreadPoolExecutor(max_workers=max(1, len(collections_to_query))) as executor:
            futures = [executor.submit(query_single_collection, col_info) for col_info in collections_to_query]
            for f in futures:
                try:
                    chunks = f.result(timeout=3.0)
                    all_chunks.extend(chunks)
                except Exception as e:
                    print(f"Collection query notice: {e}")

    # If no relevant chunks were retrieved or if all retrieved chunks have weak relevance (distance > 0.70), query all global books in catalog
    if (not all_chunks or all(c.get("distance", 1.0) > 0.70 for c in all_chunks)) and source_filter in ["all", "books"]:
        try:
            gb_res = supabase.table("global_books").select("id, title, chroma_collection_name").execute()
            if gb_res.data:
                fallback_cols = []
                already_queried = {c["name"] for c in collections_to_query}

                for gb in gb_res.data:
                    c_name = gb["chroma_collection_name"]
                    if c_name in already_queried:
                        continue
                    fallback_cols.append({
                        "name": c_name,
                        "type": "global_book",
                        "source_name": gb["title"],
                        "book_id": gb["id"],
                        "specific_source_id": None
                    })

                if fallback_cols:
                    with ThreadPoolExecutor(max_workers=max(1, len(fallback_cols))) as executor:
                        fb_futures = [executor.submit(query_single_collection, col_info) for col_info in fallback_cols]
                        for f in fb_futures:
                            try:
                                fb_chunks = f.result(timeout=3.0)
                                if fb_chunks:
                                    all_chunks.extend(fb_chunks)
                            except Exception:
                                pass
        except Exception as fb_err:
            print(f"Global catalog search fallback notice: {fb_err}")

    all_chunks.sort(key=lambda x: x["distance"])
    return all_chunks[:n_results]

def filter_active_citations(answer: str, sections_used: list) -> list:
    if not answer or not sections_used:
        return []

    answer_lower = answer.lower()
    if "cannot find" in answer_lower and "materials" in answer_lower:
        return []

    ranked = sorted(sections_used, key=lambda s: s.get("distance", 1.0))

    seen = set()
    deduped = []
    for s in ranked:
        key = (s.get("source_name", ""), str(s.get("page", s.get("source_id", ""))))
        if key not in seen:
            seen.add(key)
            deduped.append(s)

    return deduped[:4]

# In-memory cache for resolved book URLs to serve instantly without network calls
_BOOK_URL_MEMORY_CACHE: Dict[str, str] = {}

def save_book_url(book_id: str, url: str):
    _BOOK_URL_MEMORY_CACHE[book_id] = url
    mapping_path = "cache/book_urls.json"
    mapping = {}
    if os.path.exists(mapping_path):
        try:
            with open(mapping_path, "r") as f:
                mapping = json.load(f)
        except Exception:
            pass
    mapping[book_id] = url
    try:
        with open(mapping_path, "w") as f:
            json.dump(mapping, f)
    except Exception:
        pass

def get_book_url(book_id: str) -> Optional[str]:
    # 1. Fastest: in-memory cache (already resolved this session)
    if book_id in _BOOK_URL_MEMORY_CACHE:
        return _BOOK_URL_MEMORY_CACHE[book_id]
    
    # 2. Local JSON file cache
    mapping_path = "cache/book_urls.json"
    if os.path.exists(mapping_path):
        try:
            with open(mapping_path, "r") as f:
                mapping = json.load(f)
                val = mapping.get(book_id)
                if val:
                    _BOOK_URL_MEMORY_CACHE[book_id] = val  # promote to memory cache
                    return val
        except Exception:
            pass
            
    # 3. Database persistent fallback
    try:
        res = supabase.table("global_books").select("source").eq("id", book_id).execute()
        if res.data and res.data[0].get("source"):
            source_val = res.data[0]["source"]
            if "|" in source_val:
                url = source_val.split("|", 1)[1]
                _BOOK_URL_MEMORY_CACHE[book_id] = url  # promote to memory cache
                return url
    except Exception:
        pass
    return None

def resolve_ia_pdf_url(ident: str) -> Optional[str]:
    try:
        meta = requests.get(f"https://archive.org/metadata/{ident}", timeout=10).json()
        pdf_files = [f["name"] for f in meta.get("files", []) if f.get("name", "").lower().endswith(".pdf")]
        if not pdf_files:
            return None
        
        def pdf_size(name):
            for f in meta.get("files", []):
                if f.get("name") == name:
                    try:
                        return int(f.get("size", 0))
                    except Exception:
                        return 0
            return 0
        pdf_name = max(pdf_files, key=pdf_size)
        return f"https://archive.org/download/{ident}/{urllib.parse.quote(pdf_name)}"
    except Exception as e:
        print(f"Error resolving IA PDF for {ident}: {e}")
        return None

def resolve_doab_pdf(url: str) -> str:
    if "/handle/" not in url:
        return url
    handle = url.split("/handle/")[-1].strip("/")
    domain = "library.oapen.org" if "oapen.org" in url else "directory.doabooks.org"
    api_url = f"https://{domain}/rest/handle/{handle}?expand=metadata,bitstreams"
    try:
        res = requests.get(api_url, headers={"Accept": "application/json"}, timeout=10)
        if res.status_code == 200:
            data = res.json()
            # 1. Check bitstreams for pdf
            for b in data.get("bitstreams", []):
                name = b.get("name", "")
                if name.lower().endswith(".pdf") and b.get("retrieveLink"):
                    return f"https://{domain}{b.get('retrieveLink')}"
            
            # 2. Check metadata for publisher.oabooks.exampleUrl
            for m in data.get("metadata", []):
                if m.get("key") == "publisher.oabooks.exampleUrl" and m.get("value"):
                    return m.get("value")
                    
            # 3. Check metadata for dc.identifier containing pdf
            for m in data.get("metadata", []):
                if m.get("key") == "dc.identifier" and m.get("value", "").lower().endswith(".pdf"):
                    return m.get("value")
                    
            # 4. Check metadata for OAPEN handle links to recursively resolve DOAB pointers to OAPEN PDFs
            for m in data.get("metadata", []):
                val = m.get("value", "")
                if m.get("key") == "dc.identifier" and "oapen.org/handle/" in val.lower():
                    print(f"Resolving nested OAPEN handle found in DOAB metadata: {val}")
                    return resolve_doab_pdf(val)
    except Exception as e:
        print(f"Error resolving DOAB handle: {e}")
    return url

def resolve_html_to_pdf_link(url: str) -> str:
    import html
    if not url or "drive.google.com" in url or "/handle/" in url or "openstax.org" in url:
        return url
        
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        res = requests.get(url, headers=headers, timeout=10, verify=False)
        content_type = res.headers.get("content-type", "").lower()
        if "text/html" not in content_type:
            return url
            
        html_text = res.text
        
        # 1. Search for citation_pdf_url in meta tags (standard Google Scholar metadata)
        meta_matches = re.findall(r'<meta[^>]+name=["\'](?:bepress_)?citation_pdf_url["\'][^>]+content=["\']([^"\']+)["\']', html_text, re.IGNORECASE)
        if not meta_matches:
            meta_matches = re.findall(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\'](?:bepress_)?citation_pdf_url["\']', html_text, re.IGNORECASE)
            
        if meta_matches:
            resolved = urllib.parse.urljoin(url, html.unescape(meta_matches[0]))
            print(f"Resolved from citation_pdf_url meta tag: {resolved}")
            return resolved
            
        # 2. Search for link tags with type="application/pdf"
        link_matches = re.findall(r'<link[^>]+type=["\']application/pdf["\'][^>]+href=["\']([^"\']+)["\']', html_text, re.IGNORECASE)
        if not link_matches:
            link_matches = re.findall(r'<link[^>]+href=["\']([^"\']+)["\'][^>]+type=["\']application/pdf["\']', html_text, re.IGNORECASE)
        if link_matches:
            resolved = urllib.parse.urljoin(url, html.unescape(link_matches[0]))
            print(f"Resolved from link tag type application/pdf: {resolved}")
            return resolved

        # 3. Fallback to anchor tags matching criteria
        raw_links = re.findall(r'href=["\']([^"\']+)["\']', html_text, re.IGNORECASE)
        resolved_links = [urllib.parse.urljoin(url, html.unescape(rl)) for rl in raw_links]
        
        pdf_links = []
        for l in resolved_links:
            l_clean = l.split("?")[0].lower()
            if (l_clean.endswith(".pdf") or 
                "/bitstream/" in l_clean or 
                "/download" in l_clean or 
                "viewcontent.cgi" in l_clean or
                "reader" in l_clean or
                "media" in l_clean):
                if l.split("?")[0] != url.split("?")[0]:
                    pdf_links.append(l)
                    
        if pdf_links:
            direct_pdfs = [l for l in pdf_links if l.split("?")[0].lower().endswith(".pdf")]
            resolved = direct_pdfs[0] if direct_pdfs else pdf_links[0]
            print(f"Resolved from anchor tags: {resolved}")
            return resolved
    except Exception as e:
        print(f"Failed to resolve HTML page {url}: {e}")
        
    return url
