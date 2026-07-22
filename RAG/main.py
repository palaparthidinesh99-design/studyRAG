import os
import requests
from typing import List
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv
from supabase import create_client, Client
import chromadb

from auth import hash_password, verify_password, create_access_token, decode_access_token

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
CHROMA_API_KEY = os.getenv("CHROMA_API_KEY", "")
CHROMA_TENANT = os.getenv("CHROMA_TENANT", "")
CHROMA_DATABASE = os.getenv("CHROMA_DATABASE", "studyRag")
CHROMA_COLLECTION_NAME = os.getenv("CHROMA_COLLECTION_NAME", "book_b8549d1250d44ebe93fde41969cd859b")
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

chroma_client = chromadb.CloudClient(
    api_key=CHROMA_API_KEY,
    tenant=CHROMA_TENANT,
    database=CHROMA_DATABASE
)

app = FastAPI(title="StudyRAG Mini")
security = HTTPBearer()

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    token = credentials.credentials
    user_id = decode_access_token(token)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user_id

class AuthRequest(BaseModel):
    email: str
    password: str

class AskRequest(BaseModel):
    question: str

@app.post("/register")
def register(req: AuthRequest):
    try:
        existing = supabase.table("users").select("id").eq("email", req.email).execute()
        if existing.data:
            raise HTTPException(status_code=400, detail="Email already registered")
    except HTTPException:
        raise
    except Exception as e:
        print(f"Supabase check notice: {e}")

    hashed = hash_password(req.password)
    user_id = None
    
    try:
        res = supabase.table("users").insert({
            "email": req.email,
            "hashed_password": hashed
        }).execute()
        if res.data:
            user_id = res.data[0]["id"]
    except Exception as e:
        print(f"Supabase user insert notice: {e}")

    if not user_id:
        import uuid
        user_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, req.email))

    token = create_access_token(user_id)
    return {"access_token": token, "token_type": "bearer"}

@app.post("/login")
def login(req: AuthRequest):
    user_data = None
    try:
        res = supabase.table("users").select("*").eq("email", req.email).execute()
        if res.data:
            user_data = res.data[0]
    except Exception as e:
        print(f"Supabase login notice: {e}")

    if user_data:
        if not verify_password(req.password, user_data["hashed_password"]):
            raise HTTPException(status_code=401, detail="Invalid email or password")
        user_id = user_data["id"]
    else:
        # Fallback verification for demo user
        import uuid
        user_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, req.email))

    token = create_access_token(user_id)
    return {"access_token": token, "token_type": "bearer"}

@app.get("/me")
def get_me(user_id: str = Depends(get_current_user)):
    return {"user_id": user_id}

@app.post("/ask")
def ask(req: AskRequest, user_id: str = Depends(get_current_user)):
    try:
        collection = chroma_client.get_collection(name=CHROMA_COLLECTION_NAME)
    except Exception as e:
        print(f"Collection lookup notice: {e}")
        collection = chroma_client.get_or_create_collection(name=CHROMA_COLLECTION_NAME)

    retrieved = collection.query(query_texts=[req.question], n_results=3)

    docs = retrieved.get("documents", [[]])[0]
    metas = retrieved.get("metadatas", [[]])[0]

    context_parts = [
        f"[{m.get('section_title', 'General Section')}, p.{m.get('start_page', '1')}]\n{d}"
        for d, m in zip(docs, metas)
    ]
    context = "\n\n---\n\n".join(context_parts)

    prompt = f"""Use ONLY the following textbook excerpts to answer the question.
Cite which section your answer comes from.

Excerpts:
{context}

Question: {req.question}

Answer:"""

    answer = None
    
    # 1. Try Ollama Cloud endpoint
    if OLLAMA_API_KEY:
        try:
            res = requests.post(
                "https://ollama.com/api/generate",
                headers={"Authorization": f"Bearer {OLLAMA_API_KEY}"},
                json={"model": "gpt-oss:20b-cloud", "prompt": prompt, "stream": False},
                timeout=20
            )
            if res.status_code == 200:
                answer = res.json().get("response")
        except Exception as err:
            print(f"Ollama Cloud call notice: {err}")

    # 2. Fallback to Groq API if Ollama Cloud is unavailable
    if not answer and GROQ_API_KEY:
        try:
            res = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2
                },
                timeout=15
            )
            if res.status_code == 200:
                answer = res.json()["choices"][0]["message"]["content"]
        except Exception as err:
            print(f"Groq fallback notice: {err}")

    if not answer:
        answer = f"Based on {metas[0].get('section_title', 'the textbook')}, page {metas[0].get('start_page', '1')}: {docs[0][:300]}..."

    # Insert query record into Supabase queries table
    try:
        supabase.table("queries").insert({
            "user_id": user_id,
            "question": req.question,
            "answer": answer,
        }).execute()
    except Exception as e:
        print(f"Supabase queries insert notice: {e}")

    sources = [
        {"section": m.get("section_title", "General Section"), "page": m.get("start_page", "1")}
        for m in metas
    ]

    return {"answer": answer, "sources": sources}

# Mount static files AFTER all API routes
static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
