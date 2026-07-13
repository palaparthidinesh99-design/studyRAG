import os
import uuid
import io
import requests
import base64
import re
from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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

class SaveNoteRequest(BaseModel):
    title: str
    content: str

class LinkCatalogueBookRequest(BaseModel):
    source_id: str
    title: str
    pdf_url: str
    source: str

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
    # Check if subject exists and belongs to user
    subject = supabase.table("subjects").select("*").eq("id", subject_id).eq("user_id", user_id).execute()
    if not subject.data:
        raise HTTPException(status_code=404, detail="Subject not found or access denied")

    # Fast intercept for simple greetings to skip slow RAG context retrieval
    greeting_pattern = re.compile(
        r"^(hi|hello|hey|greetings|howdy|what'?s up|how are you|thanks|thank you|good morning|good afternoon|good evening)\b", 
        re.IGNORECASE
    )
    # Only intercept if it's mostly a greeting (short message)
    if greeting_pattern.match(req.query.strip()) and len(req.query.strip()) < 40:
        try:
            prompt = f"You are a friendly AI study assistant. The user said: '{req.query}'. Respond warmly, simply, and concisely."
            res = call_ollama("generate", {
                "model": "gpt-oss:20b-cloud",
                "prompt": prompt,
                "stream": False
            })
            answer = res.json()["response"].strip()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"LLM generation failed: {str(e)}")
            
        try:
            supabase.table("queries").insert({
                "subject_id": subject_id,
                "input_type": "text",
                "extracted_text": req.query,
                "generated_answer": answer,
                "sections_used": []
            }).execute()
        except Exception:
            pass
            
        return {
            "query": req.query,
            "answer": answer,
            "sources": []
        }

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
    prompt = f"""You are a friendly, helpful AI study tutor for StudyRAG.
Guidelines:
1. If the user's message is a greeting (e.g. "hi", "hello", "good morning"), a thank you, or general small talk (e.g. "how are you?", "what can you do?"), respond to them warmly, helpfully, and concisely without complaining about missing context.
2. If the user is asking a factual question about their subject, formulate a clear, detailed study answer. If the provided context is relevant, use it to ground your answer and cite which source and section it comes from. If the context does not contain the answer, you may answer from your own general knowledge, but clearly state that the information is not from the uploaded materials. Always ensure your explanations are greatly simplified, modified for clarity, and extremely easy to understand for a student. Structure the response beautifully using markdown headers (##), bold key concepts, list items, and tables where applicable to make it highly structured.

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


@app.get("/subjects/{subject_id}/books")
def list_subject_books(
    subject_id: str,
    user_id: str = Depends(get_current_user)
):
    # Confirm subject belongs to user
    subject = supabase.table("subjects").select("*").eq("id", subject_id).eq("user_id", user_id).execute()
    if not subject.data:
        raise HTTPException(status_code=404, detail="Subject not found or access denied")
        
    try:
        # Fetch the linked global books
        result = supabase.table("subject_books").select("global_books(title)").eq("subject_id", subject_id).execute()
        return [item["global_books"]["title"] for item in result.data if item.get("global_books")]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch linked books: {str(e)}")


@app.post("/subjects/{subject_id}/saved-notes")
def save_chat_note(
    subject_id: str,
    req: SaveNoteRequest,
    user_id: str = Depends(get_current_user)
):
    # 1. Confirm subject belongs to user
    subject = supabase.table("subjects").select("*").eq("id", subject_id).eq("user_id", user_id).execute()
    if not subject.data:
        raise HTTPException(status_code=404, detail="Subject not found or access denied")
        
    collection_name = subject.data[0]["chroma_collection_name"]
    
    # 2. Upload note content as a markdown file to Supabase Storage
    note_content_bytes = req.content.encode("utf-8")
    storage_path = f"{user_id}/{subject_id}/notes/{uuid.uuid4().hex}.md"
    try:
        supabase.storage.from_("user-uploads").upload(
            path=storage_path,
            file=note_content_bytes,
            file_options={"content-type": "text/markdown"}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload note to Storage: {str(e)}")
        
    # 3. Create the database record in 'sources' table
    try:
        source_insert = supabase.table("sources").insert({
            "subject_id": subject_id,
            "source_type": "saved_note",
            "title": req.title,
            "storage_path": storage_path
        }).execute()
        source_data = source_insert.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create source record: {str(e)}")
        
    # 4. Chunk and Index in Chroma Collection
    chunks = split_into_subchunks(req.content)
    if chunks:
        try:
            collection = chroma_client.get_or_create_collection(name=collection_name)
            ids = [f"source_chunk_{uuid.uuid4().hex}" for _ in range(len(chunks))]
            metadatas = [
                {
                    "source_id": source_data["id"],
                    "source_title": req.title,
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
        except Exception as e:
            print(f"Chroma DB indexing error for saved note: {e}")
            
    return {"message": "Note saved and indexed", "source": source_data}


@app.post("/subjects/{subject_id}/generate-notes")
async def generate_structured_notes(
    subject_id: str,
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user)
):
    # 1. Confirm subject belongs to user
    subject = supabase.table("subjects").select("*").eq("id", subject_id).eq("user_id", user_id).execute()
    if not subject.data:
        raise HTTPException(status_code=404, detail="Subject not found or access denied")
        
    collection_name = subject.data[0]["chroma_collection_name"]
    
    file_content = await file.read()
    file_ext = os.path.splitext(file.filename)[1].lower()
    
    # 2. Extract raw text from file
    raw_text = ""
    if file_ext == ".pdf":
        try:
            pdf_file = io.BytesIO(file_content)
            reader = PdfReader(pdf_file)
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    raw_text += text + "\n"
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to parse PDF text: {str(e)}")
    elif file_ext in [".png", ".jpg", ".jpeg", ".webp"]:
        # Run OCR
        try:
            image_b64 = base64.b64encode(file_content).decode("utf-8")
            payload = {
                "model": "gemma4:31b-cloud",
                "prompt": "Transcribe all text in this image exactly as written. Preserve paragraphs.",
                "images": [image_b64],
                "stream": False
            }
            res = call_ollama("generate", payload)
            raw_text = res.json()["response"].strip()
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"OCR transcription failed: {str(e)}")
    else:
        raise HTTPException(status_code=400, detail="Unsupported file format. Must be PDF or Image.")
        
    if not raw_text.strip():
        raise HTTPException(status_code=400, detail="No readable text found in the uploaded file.")
        
    # 3. Call LLM to restructure notes (gpt-oss:20b-cloud)
    prompt = f"""You are an expert tutor. Please reorganize, expand, and structure the following raw student notes into a highly comprehensive, detailed, and clean study guide.
Use markdown headers (##), bold key concepts, lists, and tables where applicable to make it highly structured and readable.

Raw Student Notes:
{raw_text}

Structured Study Guide:"""

    try:
        res = call_ollama("generate", {
            "model": "gpt-oss:20b-cloud",
            "prompt": prompt,
            "stream": False
        })
        structured_notes = res.json()["response"].strip()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM generation failed: {str(e)}")
        
    # 4. Upload structured markdown notes to Supabase Storage
    note_content_bytes = structured_notes.encode("utf-8")
    storage_path = f"{user_id}/{subject_id}/generated-notes/{uuid.uuid4().hex}.md"
    title = f"AI Notes - {os.path.splitext(file.filename)[0]}"
    try:
        supabase.storage.from_("user-uploads").upload(
            path=storage_path,
            file=note_content_bytes,
            file_options={"content-type": "text/markdown"}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload structured notes to Storage: {str(e)}")
        
    # 5. Create database record in 'sources' table
    try:
        source_insert = supabase.table("sources").insert({
            "subject_id": subject_id,
            "source_type": "generated_note",
            "title": title,
            "storage_path": storage_path
        }).execute()
        source_data = source_insert.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save generated note record: {str(e)}")
        
    # 6. Index structured notes in Chroma DB
    chunks = split_into_subchunks(structured_notes)
    if chunks:
        try:
            collection = chroma_client.get_or_create_collection(name=collection_name)
            ids = [f"source_chunk_{uuid.uuid4().hex}" for _ in range(len(chunks))]
            metadatas = [
                {
                    "source_id": source_data["id"],
                    "source_title": title,
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
        except Exception as e:
            print(f"Chroma DB indexing error for generated notes: {e}")
            
    return {
        "title": title,
        "content": structured_notes,
        "source": source_data
    }


@app.get("/subjects/{subject_id}/sources/{source_id}/content")
def get_source_content(
    subject_id: str,
    source_id: str,
    user_id: str = Depends(get_current_user)
):
    # Confirm subject belongs to user
    subject = supabase.table("subjects").select("*").eq("id", subject_id).eq("user_id", user_id).execute()
    if not subject.data:
        raise HTTPException(status_code=404, detail="Subject not found or access denied")
        
    # Get source details
    source = supabase.table("sources").select("*").eq("id", source_id).eq("subject_id", subject_id).execute()
    if not source.data:
        raise HTTPException(status_code=404, detail="Source not found")
        
    storage_path = source.data[0]["storage_path"]
    
    try:
        # Download file content from Supabase Storage
        file_bytes = supabase.storage.from_("user-uploads").download(storage_path)
        # Attempt to decode as text
        text_content = file_bytes.decode("utf-8")
        return {"content": text_content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch content: {str(e)}")


def fallback_process_pdf(pdf_path: str, book_title: str):
    from pypdf import PdfReader
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


def index_catalogue_book_task(global_book_id: str, pdf_url: str, title: str, collection_name: str):
    import requests
    import os
    os.makedirs("books", exist_ok=True)
    pdf_path = f"books/{global_book_id}.pdf"
    
    # 1. Download PDF
    try:
        response = requests.get(pdf_url, stream=True)
        with open(pdf_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
    except Exception as e:
        print(f"Failed to download OpenStax book {title}: {e}")
        return
        
    # 2. Process PDF
    try:
        from book_processor import process_pdf
        chunks = process_pdf(pdf_path, title)
        if not chunks:
            chunks = fallback_process_pdf(pdf_path, title)
    except Exception as e:
        print(f"Error parsing PDF outlines: {e}")
        chunks = fallback_process_pdf(pdf_path, title)
        
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
            print(f"Indexed OpenStax book {title} successfully: {len(chunks)} chunks.")
        except Exception as e:
            print(f"Chroma DB indexing error for OpenStax book: {e}")


@app.get("/catalogue/search")
def search_catalogue(query: str = ""):
    try:
        books = []
        q_lower = query.lower()
        
        # 1. Fetch OpenStax Books
        try:
            url = "https://openstax.org/apps/cms/api/v2/pages/?type=books.Book&limit=250"
            res = requests.get(url).json()
            matched_items = []
            for item in res.get("items", []):
                if not query or q_lower in item.get("title", "").lower():
                    matched_items.append(item)
                if len(matched_items) >= 10:
                    break
                    
            for item in matched_items:
                detail = requests.get(item["meta"]["detail_url"]).json()
                books.append({
                    "source_id": str(item["id"]),
                    "title": item["title"],
                    "pdf_url": detail.get("high_resolution_pdf_url"),
                    "cover_url": detail.get("cover_url"),
                    "description": detail.get("description", ""),
                    "source": "openstax"
                })
        except Exception as e:
            print(f"OpenStax search failed: {e}")

        # 2. Fetch arXiv Papers
        if query:
            try:
                import urllib.parse
                import xml.etree.ElementTree as ET
                safe_query = urllib.parse.quote(query)
                arxiv_url = f"http://export.arxiv.org/api/query?search_query=all:{safe_query}&start=0&max_results=10"
                arxiv_res = requests.get(arxiv_url).text
                
                root = ET.fromstring(arxiv_res)
                ns = {"atom": "http://www.w3.org/2005/Atom"}
                
                for entry in root.findall("atom:entry", ns):
                    title = entry.find("atom:title", ns).text.strip().replace("\\n", " ")
                    summary = entry.find("atom:summary", ns).text.strip().replace("\\n", " ")
                    pdf_url = None
                    for link in entry.findall("atom:link", ns):
                        if link.attrib.get("title") == "pdf":
                            pdf_url = link.attrib.get("href")
                            break
                            
                    if not pdf_url:
                        id_elem = entry.find("atom:id", ns)
                        if id_elem is not None:
                            pdf_url = id_elem.text.replace("abs", "pdf")
                            
                    if pdf_url:
                        pdf_url = pdf_url.replace("http://", "https://")
                        source_id = pdf_url.split("/")[-1]
                        
                        books.append({
                            "source_id": source_id,
                            "title": title,
                            "pdf_url": pdf_url,
                            "cover_url": None,
                            "description": summary,
                            "source": "arxiv"
                        })
            except Exception as e:
                print(f"arXiv search failed: {e}")
                
        return books
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch global catalogue: {str(e)}")


@app.post("/subjects/{subject_id}/books/global")
def link_catalogue_book(
    subject_id: str,
    req: LinkCatalogueBookRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user)
):
    # Confirm subject belongs to user
    subject = supabase.table("subjects").select("*").eq("id", subject_id).eq("user_id", user_id).execute()
    if not subject.data:
        raise HTTPException(status_code=404, detail="Subject not found or access denied")
        
    # Check if this book is already in global_books
    existing_book = supabase.table("global_books").select("*").eq("title", req.title).execute()
    
    if existing_book.data:
        book_id = existing_book.data[0]["id"]
    else:
        # Create a new global book entry
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
            
        # Spawn background task to download and index
        background_tasks.add_task(
            index_catalogue_book_task,
            book_id,
            req.pdf_url,
            req.title,
            collection_name
        )
        
    # Link it to the subject
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







