import re
from pypdf import PdfReader

reader = PdfReader("/Users/dinesh/Documents/projects/studyRAG/books/Introduction_to_Philosophy-WEB.pdf")
outline = reader.outline

def flatten_outline(items, reader):
    flat = []
    for item in items:
        if isinstance(item, list):
            flat.extend(flatten_outline(item, reader))
        else:
            page_num = reader.get_destination_page_number(item)
            flat.append({"title": item.title, "page": page_num})
    return flat

all_entries = flatten_outline(outline, reader)

# Keep only real numbered sections, e.g. "1.1 What Is Philosophy?"
section_pattern = re.compile(r"^\d+\.\d+\s")
sections = [e for e in all_entries if section_pattern.match(e["title"])]

# Attach an end_page to each section = next entry's page (any entry, not
# just sections, so we don't accidentally swallow "Summary" etc. into
# the previous section's text)
chunks = []
for i, section in enumerate(sections):
    start_page = section["page"]
    # find this section's position in the FULL list to get the true next boundary
    idx_in_all = all_entries.index(section)
    end_page = all_entries[idx_in_all + 1]["page"] if idx_in_all + 1 < len(all_entries) else len(reader.pages)

    text = ""
    for p in range(start_page, min(end_page, len(reader.pages))):
        page_text = reader.pages[p].extract_text()
        if page_text:
            text += page_text + "\n"

    chunks.append({
        "title": section["title"],
        "start_page": start_page,
        "end_page": end_page,
        "text": text.strip()
    })

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

# Apply this to every section chunk
final_chunks = []
for c in chunks:
    subchunks = split_into_subchunks(c["text"])
    for i, sub in enumerate(subchunks):
        final_chunks.append({
            "book": "Philosophy",  # placeholder, we'll make this dynamic later
            "section_title": c["title"],
            "start_page": c["start_page"],
            "end_page": c["end_page"],
            "subchunk_index": i,
            "text": sub
        })

print(f"Total final chunks: {len(final_chunks)}")
print(f"Average chunk length: {sum(len(c['text']) for c in final_chunks) / len(final_chunks):.0f} chars")

import chromadb

client = chromadb.PersistentClient(path="./chroma_db")  # persists to disk, not just in-memory
collection = client.get_or_create_collection(name="philosophy_book")

# Chroma needs unique string IDs per chunk
ids = [f"chunk_{i}" for i in range(len(final_chunks))]
documents = [c["text"] for c in final_chunks]
metadatas = [
    {
        "book": c["book"],
        "section_title": c["section_title"],
        "start_page": c["start_page"],
        "end_page": c["end_page"],
    }
    for c in final_chunks
]

# Chroma batches internally, but very large single calls can be slow/unstable —
# insert in batches of 100 to be safe
batch_size = 100
for i in range(0, len(final_chunks), batch_size):
    collection.add(
        ids=ids[i:i+batch_size],
        documents=documents[i:i+batch_size],
        metadatas=metadatas[i:i+batch_size],
    )
    print(f"Inserted {min(i+batch_size, len(final_chunks))}/{len(final_chunks)}")

print("Done. Collection count:", collection.count())