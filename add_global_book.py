import os
import sys
import uuid
from dotenv import load_dotenv
from supabase import create_client
import chromadb
from book_processor import process_pdf

load_dotenv()

supabase = create_client(os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY"))
chroma_client = chromadb.CloudClient(
    api_key=os.environ.get("CHROMA_API_KEY"),
    tenant=os.environ.get("CHROMA_TENANT"),
    database=os.environ.get("CHROMA_DATABASE"),
)

def add_global_book(pdf_path, book_title):
    # 1. Check it isn't already ingested
    existing = supabase.table("global_books").select("*").eq("title", book_title).execute()
    if existing.data:
        print(f"'{book_title}' already exists in global_books — skipping.")
        return existing.data[0]

    # 2. Process the PDF into chunks
    print("Processing PDF...")
    chunks = process_pdf(pdf_path, book_title)
    print(f"Created {len(chunks)} chunks.")

    # 3. Create a dedicated Chroma collection for this book
    collection_name = f"book_{uuid.uuid4().hex}"
    collection = chroma_client.get_or_create_collection(name=collection_name)

    # 4. Embed and insert in batches
    ids = [f"chunk_{i}" for i in range(len(chunks))]
    documents = [c["text"] for c in chunks]
    metadatas = [
        {
            "book": c["book"],
            "section_title": c["section_title"],
            "start_page": c["start_page"],
            "end_page": c["end_page"],
        }
        for c in chunks
    ]

    batch_size = 100
    for i in range(0, len(chunks), batch_size):
        collection.add(
            ids=ids[i:i+batch_size],
            documents=documents[i:i+batch_size],
            metadatas=metadatas[i:i+batch_size],
        )
        print(f"Embedded {min(i+batch_size, len(chunks))}/{len(chunks)}")

    # 5. Record it in Supabase
    result = supabase.table("global_books").insert({
        "title": book_title,
        "source": "openstax",
        "chroma_collection_name": collection_name,
    }).execute()

    print(f"Done. Book '{book_title}' added with {len(chunks)} chunks.")
    return result.data[0]


if __name__ == "__main__":
    pdf_path = sys.argv[1]
    book_title = sys.argv[2]
    add_global_book(pdf_path, book_title)
