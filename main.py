import os
import uuid
import io
import requests
import base64
from fastapi import FastAPI, HTTPException, Depends, UploadFile, File
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from dotenv import load_dotenv
from supabase import create_client
import chromadb
from pypdf import PdfReader
from auth import hash_password, verify_password, create_access_token, decode_access_token
from book_processor import split_into_subchunks

load_dotenv()

app = FastAPI()


@app.get("/health")
def health_check():
    return {"status": "ok"}


supabase = create_client(os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY"))
security = HTTPBearer()

chroma_client = chromadb.CloudClient(
    api_key=os.environ.get("CHROMA_API_KEY"),
    tenant=os.environ.get("CHROMA_TENANT"),
    database=os.environ.get("CHROMA_DATABASE"),
)

class RegisterRequest(BaseModel):
    email: str
    password: str

class LoginRequest(BaseModel):
    email: str
    password: str

class CreateSubjectRequest(BaseModel):
    name: str

class QueryTextRequest(BaseModel):
    query: str

@app.post("/register")
def register(req: RegisterRequest):
    existing = supabase.table("users").select("*").eq("email", req.email).execute()
    if existing.data:
        raise HTTPException(status_code=400, detail="Email already registered")

    hashed = hash_password(req.password)
    result = supabase.table("users").insert({
        "email": req.email,
        "hashed_password": hashed
    }).execute()

    user_id = result.data[0]["id"]
    token = create_access_token(user_id)
    return {"access_token": token, "token_type": "bearer"}

@app.post("/login")
def login(req: LoginRequest):
    result = supabase.table("users").select("*").eq("email", req.email).execute()
    if not result.data:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    user = result.data[0]
    if not verify_password(req.password, user["hashed_password"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(user["id"])
    return {"access_token": token, "token_type": "bearer"}

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    user_id = decode_access_token(credentials.credentials)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user_id


def retrieve_merged_context(subject_id: str, query_text: str, user_id: str, n_results: int = 5):
    # 1. Fetch the subject's private collection name
    subject = supabase.table("subjects").select("*").eq("id", subject_id).eq("user_id", user_id).execute()
    if not subject.data:
        raise HTTPException(status_code=404, detail="Subject not found or access denied")
    
    collection_name = subject.data[0]["chroma_collection_name"]
    
    # 2. Start list of collections to query with the subject's private collection
    collections_to_query = [{
        "name": collection_name,
        "type": "personal",
        "source_name": "Personal Note"
    }]
    
    # 3. Fetch linked global books
    linked_books = supabase.table("subject_books").select("global_book_id").eq("subject_id", subject_id).execute()
    if linked_books.data:
        book_ids = [lb["global_book_id"] for lb in linked_books.data]
        books = supabase.table("global_books").select("title", "chroma_collection_name").in_("id", book_ids).execute()
        for b in books.data:
            collections_to_query.append({
                "name": b["chroma_collection_name"],
                "type": "global_book",
                "source_name": b["title"]
            })
            
    # 4. Query each collection and collect results
    all_chunks = []
    for col_info in collections_to_query:
        try:
            col = chroma_client.get_or_create_collection(name=col_info["name"])
            results = col.query(query_texts=[query_text], n_results=n_results)
            
            if results and results.get("documents") and len(results["documents"]) > 0:
                docs = results["documents"][0]
                metas = results["metadatas"][0] if results.get("metadatas") else [None] * len(docs)
                dists = results["distances"][0] if results.get("distances") else [0.0] * len(docs)
                
                for doc, meta, dist in zip(docs, metas, dists):
                    all_chunks.append({
                        "document": doc,
                        "metadata": meta,
                        "distance": dist,
                        "source_type": col_info["type"],
                        "source_name": col_info["source_name"]
                    })
        except Exception as e:
            print(f"Error querying Chroma collection {col_info['name']}: {e}")
            
    # 5. Sort all chunks by distance ascending (closest first)
    all_chunks.sort(key=lambda x: x["distance"])
    
    # Return the top n_results
    return all_chunks[:n_results]


def call_ollama(endpoint: str, payload: dict) -> requests.Response:
    base_url = os.environ.get("OLLAMA_URL", "http://localhost:11434/api")
    url = f"{base_url}/{endpoint}"
    
    headers = {}
    api_key = os.environ.get("OLLAMA_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        
    res = requests.post(url, json=payload, headers=headers)
    res.raise_for_status()
    return res


@app.get("/me")
def read_current_user(user_id: str = Depends(get_current_user)):
    return {"user_id": user_id}

@app.post("/subjects")
def create_subject(req: CreateSubjectRequest, user_id: str = Depends(get_current_user)):
    collection_name = f"subject_{uuid.uuid4().hex}"
    chroma_client.get_or_create_collection(name=collection_name)

    result = supabase.table("subjects").insert({
        "user_id": user_id,
        "name": req.name,
        "chroma_collection_name": collection_name,
    }).execute()

    return result.data[0]

@app.get("/subjects")
def list_subjects(user_id: str = Depends(get_current_user)):
    result = supabase.table("subjects").select("*").eq("user_id", user_id).execute()
    return result.data

@app.get("/global-books")
def list_global_books():
    result = supabase.table("global_books").select("*").execute()
    return result.data

@app.post("/subjects/{subject_id}/books/{global_book_id}")
def link_book_to_subject(subject_id: str, global_book_id: str, user_id: str = Depends(get_current_user)):
    # Confirm the subject actually belongs to this user
    subject = supabase.table("subjects").select("*").eq("id", subject_id).eq("user_id", user_id).execute()
    if not subject.data:
        raise HTTPException(status_code=404, detail="Subject not found")

    # Confirm the book exists
    book = supabase.table("global_books").select("*").eq("id", global_book_id).execute()
    if not book.data:
        raise HTTPException(status_code=404, detail="Book not found")

    result = supabase.table("subject_books").insert({
        "subject_id": subject_id,
        "global_book_id": global_book_id,
    }).execute()

    return {"message": "Book linked", "data": result.data}


@app.post("/subjects/{subject_id}/sources")
async def upload_source(
    subject_id: str,
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user)
):
    # 1. Confirm the subject belongs to the current user
    subject = supabase.table("subjects").select("*").eq("id", subject_id).eq("user_id", user_id).execute()
    if not subject.data:
        raise HTTPException(status_code=404, detail="Subject not found or access denied")
    
    collection_name = subject.data[0]["chroma_collection_name"]
    
    # Read file content and determine type
    file_content = await file.read()
    file_ext = os.path.splitext(file.filename)[1].lower()
    
    if file_ext == ".pdf":
        source_type = "text_pdf"
    elif file_ext in [".png", ".jpg", ".jpeg", ".webp"]:
        source_type = "image_ocr"
    else:
        raise HTTPException(status_code=400, detail="Unsupported file format. Must be PDF or image.")
    
    # 2. Upload file to Supabase Storage in 'user-uploads' bucket
    storage_path = f"{user_id}/{subject_id}/{uuid.uuid4().hex}{file_ext}"
    try:
        supabase.storage.from_("user-uploads").upload(
            path=storage_path,
            file=file_content,
            file_options={"content-type": file.content_type}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload to Supabase Storage: {str(e)}")
        
    # 3. Create the database record in 'sources' table
    try:
        source_insert = supabase.table("sources").insert({
            "subject_id": subject_id,
            "source_type": source_type,
            "title": file.filename,
            "storage_path": storage_path
        }).execute()
        source_data = source_insert.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create database record: {str(e)}")
        
    # 4. Extract text from the source
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
            raise HTTPException(status_code=400, detail=f"Failed to parse PDF text layer: {str(e)}")
    else:
        # Call the local/cloud VLM (Ollama with gemma4:31b-cloud)
        try:
            image_b64 = base64.b64encode(file_content).decode("utf-8")
            payload = {
                "model": "gemma4:31b-cloud",
                "prompt": "Transcribe all text in this image exactly as written. Preserve paragraphs and layout as much as possible.",
                "images": [image_b64],
                "stream": False
            }
            res = call_ollama("generate", payload)
            extracted_text = res.json()["response"].strip()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"OCR transcription failed: {str(e)}")
            
    # 5. Chunk and Ingest into Chroma Collection
    chunks = split_into_subchunks(extracted_text)
    chunks_inserted = 0
    
    if chunks:
        collection = chroma_client.get_or_create_collection(name=collection_name)
        ids = [f"source_chunk_{uuid.uuid4().hex}" for _ in range(len(chunks))]
        metadatas = [
            {
                "source_id": source_data["id"],
                "source_title": file.filename,
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
        chunks_inserted = len(chunks)
        
    return {
        "message": "Source uploaded and indexed",
        "source": source_data,
        "chunks_indexed": chunks_inserted
    }


@app.post("/subjects/{subject_id}/query/text")
def query_text(
    subject_id: str,
    req: QueryTextRequest,
    user_id: str = Depends(get_current_user)
):
    # 1. Retrieve merged context
    retrieved = retrieve_merged_context(subject_id, req.query, user_id)
    
    # 2. Format context for prompt
    context_parts = []
    sections_used = []
    
    for chunk in retrieved:
        doc = chunk["document"]
        meta = chunk["metadata"]
        source_name = chunk["source_name"]
        
        if chunk["source_type"] == "global_book":
            section = meta.get("section_title", "Unknown Section")
            page = meta.get("start_page", "Unknown")
            ref_str = f"[{source_name}, {section}, p.{page}]"
            sections_used.append({
                "source_type": "global_book",
                "source_name": source_name,
                "section": section,
                "page": page
            })
        else:
            ref_str = f"[Upload: {meta.get('source_title', 'Personal Note')}]"
            sections_used.append({
                "source_type": "personal",
                "source_name": meta.get('source_title', 'Personal Note')
            })
            
        context_parts.append(f"{ref_str}\n{doc}")
        
    context = "\n\n---\n\n".join(context_parts)
    
    # 3. Call LLM (gpt-oss:20b-cloud)
    prompt = f"""Use ONLY the following context to answer the question.
Cite which source and section your answer comes from. Make clear, formatted notes.

Context:
{context}

Question: {req.query}

Answer:"""
    
    try:
        res = call_ollama("generate", {
            "model": "gpt-oss:20b-cloud",
            "prompt": prompt,
            "stream": False
        })
        answer = res.json()["response"].strip()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM generation failed: {str(e)}")
        
    # 4. Save to queries table in database
    try:
        supabase.table("queries").insert({
            "subject_id": subject_id,
            "input_type": "text",
            "extracted_text": req.query,
            "generated_answer": answer,
            "sections_used": sections_used
        }).execute()
    except Exception as e:
        print(f"Failed to log query to DB: {e}")
        
    return {
        "query": req.query,
        "answer": answer,
        "sources": sections_used
    }


@app.post("/subjects/{subject_id}/query/photo")
async def query_photo(
    subject_id: str,
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user)
):
    # 1. Confirm subject belongs to user
    subject = supabase.table("subjects").select("*").eq("id", subject_id).eq("user_id", user_id).execute()
    if not subject.data:
        raise HTTPException(status_code=404, detail="Subject not found or access denied")
    
    file_content = await file.read()
    file_ext = os.path.splitext(file.filename)[1].lower()
    if file_ext not in [".png", ".jpg", ".jpeg", ".webp"]:
        raise HTTPException(status_code=400, detail="Invalid image format. Must be PNG, JPG, JPEG or WEBP.")
    
    # 2. Upload photo to Supabase Storage (in user-uploads bucket)
    storage_path = f"{user_id}/{subject_id}/queries/{uuid.uuid4().hex}{file_ext}"
    try:
        supabase.storage.from_("user-uploads").upload(
            path=storage_path,
            file=file_content,
            file_options={"content-type": file.content_type}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload image to Supabase Storage: {str(e)}")
        
    # 3. OCR Transcription using local/cloud VLM (Ollama with gemma4:31b-cloud)
    try:
        image_b64 = base64.b64encode(file_content).decode("utf-8")
        payload = {
            "model": "gemma4:31b-cloud",
            "prompt": "Transcribe all text in this image exactly as written. If it is a question, return just the question.",
            "images": [image_b64],
            "stream": False
        }
        res = call_ollama("generate", payload)
        extracted_text = res.json()["response"].strip()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"OCR transcription failed: {str(e)}")
        
    # 4. Retrieve context
    retrieved = retrieve_merged_context(subject_id, extracted_text, user_id)
    
    # 5. Format context for prompt
    context_parts = []
    sections_used = []
    
    for chunk in retrieved:
        doc = chunk["document"]
        meta = chunk["metadata"]
        source_name = chunk["source_name"]
        
        if chunk["source_type"] == "global_book":
            section = meta.get("section_title", "Unknown Section")
            page = meta.get("start_page", "Unknown")
            ref_str = f"[{source_name}, {section}, p.{page}]"
            sections_used.append({
                "source_type": "global_book",
                "source_name": source_name,
                "section": section,
                "page": page
            })
        else:
            ref_str = f"[Upload: {meta.get('source_title', 'Personal Note')}]"
            sections_used.append({
                "source_type": "personal",
                "source_name": meta.get('source_title', 'Personal Note')
            })
            
        context_parts.append(f"{ref_str}\n{doc}")
        
    context = "\n\n---\n\n".join(context_parts)
    
    # 6. Call LLM (gpt-oss:20b-cloud)
    prompt = f"""Use ONLY the following context to answer the question.
Cite which source and section your answer comes from. Make clear, formatted notes.

Context:
{context}

Question: {extracted_text}

Answer:"""
    
    try:
        res = call_ollama("generate", {
            "model": "gpt-oss:20b-cloud",
            "prompt": prompt,
            "stream": False
        })
        answer = res.json()["response"].strip()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM generation failed: {str(e)}")
        
    # 7. Save to queries table in database
    try:
        supabase.table("queries").insert({
            "subject_id": subject_id,
            "input_type": "photo",
            "input_storage_path": storage_path,
            "extracted_text": extracted_text,
            "generated_answer": answer,
            "sections_used": sections_used
        }).execute()
    except Exception as e:
        print(f"Failed to log query to DB: {e}")
        
    return {
        "extracted_text": extracted_text,
        "answer": answer,
        "sources": sections_used
    }


@app.get("/subjects/{subject_id}/history")
def get_subject_history(
    subject_id: str,
    user_id: str = Depends(get_current_user)
):
    # 1. Confirm subject belongs to user
    subject = supabase.table("subjects").select("*").eq("id", subject_id).eq("user_id", user_id).execute()
    if not subject.data:
        raise HTTPException(status_code=404, detail="Subject not found or access denied")
        
    # 2. Fetch queries from database sorted by created_at descending
    try:
        result = supabase.table("queries").select("*").eq("subject_id", subject_id).order("created_at", desc=True).execute()
        return result.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch query history: {str(e)}")


@app.get("/subjects/{subject_id}/sources")
def list_subject_sources(
    subject_id: str,
    user_id: str = Depends(get_current_user)
):
    # Confirm subject belongs to user
    subject = supabase.table("subjects").select("*").eq("id", subject_id).eq("user_id", user_id).execute()
    if not subject.data:
        raise HTTPException(status_code=404, detail="Subject not found or access denied")
        
    try:
        result = supabase.table("sources").select("*").eq("subject_id", subject_id).execute()
        return result.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch sources: {str(e)}")




