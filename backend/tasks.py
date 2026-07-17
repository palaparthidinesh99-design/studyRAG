import os
import io
import uuid
import base64
import requests
import urllib.parse
from pypdf import PdfReader
from backend.config import chroma_client
from backend.processors import split_into_subchunks, process_pdf, fallback_process_pdf
from backend.db_helpers import save_book_url, resolve_ia_pdf_url, resolve_doab_pdf, resolve_html_to_pdf_link

def index_source_task(
    source_id: str,
    subject_id: str,
    file_content: bytes,
    filename: str,
    collection_name: str,
    source_type: str
):
    try:
        extracted_text = ""
        if source_type == "text_pdf":
            try:
                pdf_file = io.BytesIO(file_content)
                reader = PdfReader(pdf_file)
                for page in reader.pages:
                    text = page.extract_text()
                    if text:
                        extracted_text += text + "\n"
            except Exception as e:
                print(f"Background indexing: Failed to parse PDF text: {e}")
        else:
            try:
                from backend.llm import call_groq_vision, compress_image
                try:
                    file_content = compress_image(file_content)
                except Exception:
                    pass
                image_b64 = base64.b64encode(file_content).decode("utf-8")
                extracted_text = call_groq_vision(
                    "Transcribe all text in this image exactly as written. Preserve paragraphs and layout as much as possible.",
                    image_b64
                )
            except Exception as e:
                print(f"Background indexing: OCR transcription failed: {e}")
                
        if extracted_text.strip():
            chunks = split_into_subchunks(extracted_text)
            if chunks:
                collection = chroma_client.get_or_create_collection(name=collection_name)
                ids = [f"source_chunk_{uuid.uuid4().hex}" for _ in range(len(chunks))]
                metadatas = [
                    {
                        "source_id": source_id,
                        "source_title": filename,
                        "chunk_index": i
                    }
                    for i in range(len(chunks))
                ]
                
                batch_size = 100
                for i in range(0, len(chunks), batch_size):
                    collection.add(
                        ids=ids[i:i+batch_size],
                        documents=chunks[i:i+batch_size],
                        metadatas=metadatas[i:i+batch_size]
                    )
                print(f"Background indexing completed successfully for {filename}: {len(chunks)} chunks.")
    except Exception as general_err:
        print(f"Background indexing task failed: {general_err}")


def index_catalogue_book_task(global_book_id: str, pdf_url: str, title: str, collection_name: str):
    os.makedirs("books", exist_ok=True)
    pdf_path = f"books/{global_book_id}.pdf"
    
    if pdf_url and "archive.org/details/" in pdf_url:
        ident = pdf_url.split("archive.org/details/")[-1].strip("/")
        resolved = resolve_ia_pdf_url(ident)
        if resolved:
            pdf_url = resolved
            save_book_url(global_book_id, pdf_url)
        else:
            print(f"Could not resolve IA PDF for task: {ident}")
            return
            
    # Resolve dynamic OpenStax redirect links to static direct PDF URLs
    if pdf_url and ("openstax.org/downloads/download" in pdf_url or "openstax.org/downloads" in pdf_url):
        try:
            print(f"Resolving dynamic OpenStax PDF link for: {title}")
            cms_url = "https://openstax.org/apps/cms/api/v2/pages/?type=books.Book&limit=250"
            cms_res = requests.get(cms_url, timeout=10).json()
            matched_page = None
            clean_title = title.replace("[by OpenStax]", "").strip().lower()
            for item in cms_res.get("items", []):
                item_title = item["title"].lower().strip()
                if clean_title in item_title or item_title in clean_title:
                    matched_page = item
                    break
            
            if matched_page:
                detail_res = requests.get(matched_page["meta"]["detail_url"], timeout=10).json()
                direct_url = detail_res.get("high_resolution_pdf_url") or detail_res.get("pdf_url")
                if direct_url and "assets.openstax.org" in direct_url:
                    print(f"Successfully resolved dynamic link to static PDF URL: {direct_url}")
                    pdf_url = direct_url
                    save_book_url(global_book_id, pdf_url)
        except Exception as resolve_err:
            print(f"Failed to dynamically resolve OpenStax PDF link: {resolve_err}")
            
    # Resolve DOAB/OAPEN handle links to direct PDF links
    if pdf_url and "/handle/" in pdf_url:
        try:
            print(f"Resolving DOAB/OAPEN handle link for: {title}")
            resolved_pdf = resolve_doab_pdf(pdf_url)
            if resolved_pdf != pdf_url:
                pdf_url = resolved_pdf
                save_book_url(global_book_id, pdf_url)
                print(f"Successfully resolved DOAB handle to direct link: {pdf_url}")
        except Exception as resolve_err:
            print(f"Failed to resolve DOAB handle: {resolve_err}")
            
    # Resolve any remaining HTML landing page links (like OTL, LibreTexts) using citation_pdf_url metadata crawling
    if pdf_url:
        try:
            resolved_pdf = resolve_html_to_pdf_link(pdf_url)
            if resolved_pdf != pdf_url:
                pdf_url = resolved_pdf
                save_book_url(global_book_id, pdf_url)
                print(f"Successfully crawled/resolved HTML landing page to direct link: {pdf_url}")
        except Exception as resolve_err:
            print(f"Failed to crawl/resolve HTML landing page link: {resolve_err}")
            
    file_path = None
    try:
        # 1. Download PDF/Text
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        try:
            response = requests.get(pdf_url, stream=True, headers=headers, timeout=30, allow_redirects=True, verify=False)
            response.raise_for_status()
        except Exception as conn_err:
            print(f"Failed download with headers: {conn_err}. Retrying simple requests.get...")
            response = requests.get(pdf_url, stream=True, timeout=35, allow_redirects=True, verify=False)
            response.raise_for_status()
        
        content_type = response.headers.get("content-type", "").lower()
        if "text/html" in content_type:
            try:
                html_text = requests.get(pdf_url, headers=headers, timeout=15, verify=False).text
                import re
                import urllib.parse
                
                raw_links = re.findall(r'href=["\']([^"\']+)["\']', html_text, re.IGNORECASE)
                resolved_links = []
                for rl in raw_links:
                    abs_url = urllib.parse.urljoin(pdf_url, rl)
                    resolved_links.append(abs_url)
                
                pdf_links = []
                for link in resolved_links:
                    lower_link = link.lower()
                    if ".pdf" in lower_link or "bitstream" in lower_link or "download" in lower_link:
                        if link.split("?")[0] != pdf_url.split("?")[0]:
                            pdf_links.append(link)
                
                if pdf_links:
                    direct_pdfs = [l for l in pdf_links if ".pdf" in l.lower()]
                    crawled_url = direct_pdfs[0] if direct_pdfs else pdf_links[0]
                    print(f"Crawled and resolved direct PDF link: {crawled_url}")
                    save_book_url(global_book_id, crawled_url)
                    response = requests.get(crawled_url, stream=True, headers=headers, timeout=30, allow_redirects=True, verify=False)
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").lower()
            except Exception as crawl_err:
                print(f"Failed to crawl landing page for PDF links: {crawl_err}")
                
        is_text = "text/plain" in content_type or pdf_url.endswith(".txt") or ".txt." in pdf_url
        file_ext = ".txt" if is_text else ".pdf"
        file_path = f"books/{global_book_id}{file_ext}"
        
        with open(file_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
                
        # 2. Process File
        chunks = []
        if file_path.endswith(".txt"):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    text_content = f.read()
                lines = text_content.split('\n')
                current_chunk = []
                current_length = 0
                chunk_index = 1
                for line in lines:
                    current_chunk.append(line)
                    current_length += len(line)
                    if current_length > 1000:
                        chunks.append({
                            "text": "\n".join(current_chunk),
                            "start_page": f"Part {chunk_index}",
                            "end_page": f"Part {chunk_index}",
                            "section_title": title,
                            "subchunk_index": 0
                        })
                        current_chunk = []
                        current_length = 0
                        chunk_index += 1
                if current_chunk:
                    chunks.append({
                        "text": "\n".join(current_chunk),
                        "start_page": f"Part {chunk_index}",
                        "end_page": f"Part {chunk_index}",
                        "section_title": title,
                        "subchunk_index": 0
                    })
            except Exception as e:
                print(f"Failed to process text file: {e}")
        else:
            try:
                chunks = process_pdf(file_path, title)
                if not chunks:
                    chunks = fallback_process_pdf(file_path, title)
            except Exception as e:
                print(f"Error parsing PDF outlines: {e}")
                chunks = fallback_process_pdf(file_path, title)
            
        # 3. Index in Chroma DB
        if chunks:
            try:
                collection = chroma_client.get_or_create_collection(name=collection_name)
                ids = [f"book_chunk_{uuid.uuid4().hex}" for _ in range(len(chunks))]
                metadatas = [
                    {
                        "source_id": global_book_id,
                        "source_title": title,
                        "section_title": chunk["section_title"],
                        "start_page": chunk["start_page"],
                        "end_page": chunk["end_page"],
                        "subchunk_index": chunk["subchunk_index"]
                    }
                    for chunk in chunks
                ]
                documents = [chunk["text"] for chunk in chunks]
                
                batch_size = 100
                for i in range(0, len(documents), batch_size):
                    collection.add(
                        ids=ids[i:i+batch_size],
                        documents=documents[i:i+batch_size],
                        metadatas=metadatas[i:i+batch_size]
                    )
                print(f"Indexed book {title} successfully: {len(chunks)} chunks.")
            except Exception as e:
                print(f"Chroma DB indexing error for book: {e}")
    except Exception as e:
        print(f"Error processing index book task: {e}")
    finally:
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
                print(f"Deleted local book file {file_path} to conserve server disk space.")
            except Exception as cleanup_err:
                print(f"Failed to delete local book file {file_path}: {cleanup_err}")
