import re
from pypdf import PdfReader
from book_processor import flatten_outline, split_into_subchunks, process_pdf

def fallback_process_pdf(pdf_path: str, book_title: str):
    reader = PdfReader(pdf_path)
    chunks = []
    for page_idx, page in enumerate(reader.pages):
        text = page.extract_text()
        if not text:
            continue
        subchunks = split_into_subchunks(text.strip())
        for i, sub in enumerate(subchunks):
            chunks.append({
                "book": book_title,
                "section_title": f"Page {page_idx + 1}",
                "start_page": page_idx + 1,
                "end_page": page_idx + 1,
                "subchunk_index": i,
                "text": sub
            })
    return chunks
