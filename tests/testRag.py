import chromadb
import requests
import base64

client = chromadb.PersistentClient(path="./chroma_db")
collection = client.get_or_create_collection(name="philosophy_book")

def ocr_photo(image_path):
    with open(image_path, "rb") as f:
        image_data = base64.b64encode(f.read()).decode("utf-8")

    url = "http://localhost:11434/api/generate"
    payload = {
        "model": "gemma4:31b-cloud",
        "prompt": "Transcribe all text in this image exactly as written. If it's a question, return just the question.",
        "images": [image_data],
        "stream": False
    }
    response = requests.post(url, json=payload)
    return response.json()["response"].strip()

def retrieve(question, n_results=3):
    return collection.query(query_texts=[question], n_results=n_results)

def generate_answer(question, retrieved):
    context_parts = []
    for doc, meta in zip(retrieved["documents"][0], retrieved["metadatas"][0]):
        context_parts.append(f"[{meta['section_title']}, p.{meta['start_page']}]\n{doc}")
    context = "\n\n---\n\n".join(context_parts)

    prompt = f"""Use ONLY the following textbook excerpts to answer the question.
Cite which section your answer comes from, make clear formatted notes .

Excerpts:
{context}

Question: {question}

Answer:"""

    url = "http://localhost:11434/api/generate"
    payload = {"model": "gpt-oss:20b-cloud", "prompt": prompt, "stream": False}
    response = requests.post(url, json=payload)
    return response.json()["response"]


# --- Full pipeline test ---
image_path = "/Users/dinesh/Documents/projects/studyRAG/indianPhilosophy.jpg"

print("Reading photo...")
question = ocr_photo(image_path)
print(f"OCR'd question: {question}\n")

print("Retrieving relevant passages...")
retrieved = retrieve(question)
for meta in retrieved["metadatas"][0]:
    print(f"- {meta['section_title']} (p.{meta['start_page']}-{meta['end_page']})")

print("\nGenerating explanation...")
answer = generate_answer(question, retrieved)
print(f"\n--- Answer ---\n{answer}")