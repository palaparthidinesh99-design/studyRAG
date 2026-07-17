import os
import io
import uuid
import gc
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
    cloudinary_url: str,  # Cloudinary URL (not raw bytes) to avoid RAM bloat
    filename: str,
    collection_name: str,
    source_type: str
):
    """Background task: download file from Cloudinary URL, extract text, index in Chroma.
    
    We pass the Cloudinary URL instead of raw bytes to avoid holding large binary data
    in the BackgroundTask closure after the HTTP request completes.
    """
    try:
        file_content = None
        extracted_text = ""

        if source_type == "text_pdf":
            try:
                # Stream the PDF directly without loading the full response into memory at once
                response = requests.get(cloudinary_url, stream=True, timeout=60)
                response.raise_for_status()
                pdf_bytes = response.content
                pdf_file = io.BytesIO(pdf_bytes)
                reader = PdfReader(pdf_file)
                for page in reader.pages:
                    text = page.extract_text()
                    if text:
                        extracted_text += text + "\n"
                # Immediately release large objects
                del pdf_bytes
                del pdf_file
                del reader
                gc.collect()
            except Exception as e:
                print(f"Background indexing: Failed to parse PDF text: {e}")
        else:
            try:
                from backend.llm import call_groq_vision, compress_image
                response = requests.get(cloudinary_url, timeout=60)
                response.raise_for_status()
                img_bytes = compress_image(response.content)
                del response
                image_b64 = base64.b64encode(img_bytes).decode("utf-8")
                del img_bytes
                gc.collect()
                extracted_text = call_groq_vision(
                    "Transcribe all text in this image exactly as written. Preserve paragraphs and layout as much as possible.",
                    image_b64
                )
                del image_b64
                gc.collect()
            except Exception as e:
                print(f"Background indexing: OCR transcription failed: {e}")

        if extracted_text.strip():
            chunks = split_into_subchunks(extracted_text)
            del extracted_text
            gc.collect()

            if chunks:
                from backend.llm import call_gemini_embeddings
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
                    batch_docs = chunks[i:i+batch_size]
                    # Pre-calculate Gemini embeddings to avoid local ONNX load / CPU bloat
                    embeddings = call_gemini_embeddings(batch_docs)
                    
                    collection.add(
                        ids=ids[i:i+batch_size],
                        documents=batch_docs,
                        metadatas=metadatas[i:i+batch_size],
                        embeddings=embeddings
                    )
                print(f"Background indexing completed successfully for {filename}: {len(chunks)} chunks.")
                del chunks, ids, metadatas
                gc.collect()
    except Exception as general_err:
        print(f"Background indexing task failed: {general_err}")


def index_catalogue_book_task(global_book_id: str, pdf_url: str, title: str, collection_name: str):
    os.makedirs("books", exist_ok=True)
    pdf_path = f"books/{global_book_id}.pdf"
    
    import json
    from backend.config import supabase

    # Resolve DOAB/OAPEN handles before downloading
    if "/handle/" in pdf_url:
        try:
            resolved_url = resolve_doab_pdf(pdf_url)
            if resolved_url != pdf_url:
                pdf_url = resolved_url
                save_book_url(global_book_id, pdf_url)
                print(f"Successfully resolved DOAB handle to: {pdf_url}")
        except Exception as resolve_err:
            print(f"Failed to resolve DOAB handle: {resolve_err}")

    # Resolve Internet Archive identifiers
    if "archive.org/details/" in pdf_url:
        try:
            ident = pdf_url.rstrip("/").split("/")[-1]
            resolved_url = resolve_ia_pdf_url(ident)
            if resolved_url:
                pdf_url = resolved_url
                save_book_url(global_book_id, pdf_url)
        except Exception as resolve_err:
            print(f"Failed to resolve IA identifier: {resolve_err}")

    # Resolve HTML landing pages to direct PDF links
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
                import re as _re
                raw_links = _re.findall(r'href=["\'"]([^"\'"]+)["\'""]', html_text, _re.IGNORECASE)
                resolved_links = [urllib.parse.urljoin(pdf_url, rl) for rl in raw_links]
                
                pdf_links = [
                    link for link in resolved_links
                    if (".pdf" in link.lower() or "bitstream" in link.lower() or "download" in link.lower())
                    and link.split("?")[0] != pdf_url.split("?")[0]
                ]
                
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
        del response
        gc.collect()
                
        # 2. Process File
        chunks = []
        if file_path.endswith(".txt"):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    text_content = f.read()
                lines = text_content.split('\n')
                del text_content
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
                del lines
                gc.collect()
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
            
            # Explicit garbage collection to release PyPDF memory structures early
            gc.collect()
            
        # 3. Index in Chroma DB
        if chunks:
            try:
                from backend.llm import call_gemini_embeddings
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
                    batch_docs = documents[i:i+batch_size]
                    # Pre-calculate Gemini embeddings to avoid local ONNX load / CPU bloat
                    embeddings = call_gemini_embeddings(batch_docs)
                    
                    collection.add(
                        ids=ids[i:i+batch_size],
                        documents=batch_docs,
                        metadatas=metadatas[i:i+batch_size],
                        embeddings=embeddings
                    )
                print(f"Indexed book {title} successfully: {len(chunks)} chunks.")
                # Free memory references immediately
                chunks = None
                documents = None
                ids = None
                metadatas = None
                gc.collect()
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
