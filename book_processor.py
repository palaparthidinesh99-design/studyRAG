import re
from pypdf import PdfReader

def flatten_outline(items, reader):
    flat = []
    for item in items:
        if isinstance(item, list):
            flat.extend(flatten_outline(item, reader))
        else:
            page_num = reader.get_destination_page_number(item)
            flat.append({"title": item.title, "page": page_num})
    return flat

def split_into_subchunks(text, max_chars=1500):
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    subchunks = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) < max_chars:
            current += " " + para
        else:
            if current:
                subchunks.append(current.strip())
            current = para
    if current:
        subchunks.append(current.strip())
    return subchunks

def process_pdf(pdf_path, book_title):
    reader = PdfReader(pdf_path)
    outline = reader.outline
    all_entries = flatten_outline(outline, reader)

    section_pattern = re.compile(r"^\d+\.\d+\s")
    sections = [e for e in all_entries if section_pattern.match(e["title"])]

    final_chunks = []
    for section in sections:
        start_page = section["page"]
        idx_in_all = all_entries.index(section)
        end_page = all_entries[idx_in_all + 1]["page"] if idx_in_all + 1 < len(all_entries) else len(reader.pages)

        text = ""
        for p in range(start_page, min(end_page, len(reader.pages))):
            page_text = reader.pages[p].extract_text()
            if page_text:
                text += page_text + "\n"

        subchunks = split_into_subchunks(text.strip())
        for i, sub in enumerate(subchunks):
            final_chunks.append({
                "book": book_title,
                "section_title": section["title"],
                "start_page": start_page,
                "end_page": end_page,
                "subchunk_index": i,
                "text": sub
            })

    return final_chunks
    