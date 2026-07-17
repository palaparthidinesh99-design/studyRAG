import os
import random
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from backend.config import supabase
from backend.models import RegisterRequest, LoginRequest, VerifyEmailRequest, ResendCodeRequest
from backend.auth import hash_password, verify_password, create_access_token, get_current_user

# Import routers
from backend.routes.subjects import router as subjects_router
from backend.routes.sources import router as sources_router
from backend.routes.books import router as books_router
from backend.routes.queries import router as queries_router

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Root & Health Endpoints
@app.get("/")
def read_root():
    return {"status": "healthy", "message": "StudyRAG FastAPI Backend is fully operational."}

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.get("/test-keys")
def test_keys():
    import os
    from backend.llm import call_gemini, call_groq
    
    google_key = os.environ.get("GOOGLE_API_KEY", "")
    groq_key = os.environ.get("GROQ_API_KEY", "")
    
    gemini_status = "Not tested"
    try:
        if google_key:
            res = call_gemini([{"role": "user", "content": "Hi"}], model="gemini-2.5-flash", max_tokens=10)
            gemini_status = f"Success: {res}"
        else:
            gemini_status = "Google Key missing"
    except Exception as e:
        gemini_status = f"Failed: {str(e)}"
        
    groq_status = "Not tested"
    try:
        if groq_key:
            res = call_groq([{"role": "user", "content": "Hi"}], model="llama3-8b-8192", max_tokens=10)
            groq_status = f"Success: {res}"
        else:
            groq_status = "Groq Key missing"
    except Exception as e:
        groq_status = f"Failed: {str(e)}"
        
    return {
        "google_key_configured": bool(google_key),
        "google_key_prefix": google_key[:8] if google_key else "",
        "groq_key_configured": bool(groq_key),
        "groq_key_prefix": groq_key[:8] if groq_key else "",
        "gemini_test": gemini_status,
        "groq_test": groq_status
    }

# Authentication Endpoints
@app.post("/register")
def register(req: RegisterRequest):
    existing = supabase.table("users").select("*").eq("email", req.email).execute()
    if existing.data:
        raise HTTPException(status_code=400, detail="Email already registered")

    hashed = hash_password(req.password)
    # Store with verification status pre-approved: hashed_password|name|true|
    db_password_field = f"{hashed}|{req.name or ''}|true|"
    
    try:
        result = supabase.table("users").insert({
            "email": req.email,
            "hashed_password": db_password_field
        }).execute()
        user = result.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create user: {str(e)}")

    token = create_access_token(user["id"])
    return {"access_token": token, "token_type": "bearer", "status": "verified"}

@app.post("/verify-email")
def verify_email(req: VerifyEmailRequest):
    # Legacy endpoint: always return successful verification
    result = supabase.table("users").select("*").eq("email", req.email).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="User not found")
    user = result.data[0]
    token = create_access_token(user["id"])
    return {"access_token": token, "token_type": "bearer", "status": "verified"}

@app.post("/resend-code")
def resend_code(req: ResendCodeRequest):
    # Legacy endpoint: always return OK
    return {"status": "ok", "message": "Email is already verified."}

@app.post("/login")
def login(req: LoginRequest):
    result = supabase.table("users").select("*").eq("email", req.email).execute()
    if not result.data:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    user = result.data[0]
    db_hashed = user["hashed_password"]
    parts = db_hashed.split("|")
    hashed_password = parts[0]

    if not verify_password(req.password, hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(user["id"])
    return {"access_token": token, "token_type": "bearer"}

@app.get("/me")
def read_current_user(user_id: str = Depends(get_current_user)):
    user = supabase.table("users").select("id", "email", "hashed_password").eq("id", user_id).execute()
    if not user.data:
        raise HTTPException(status_code=401, detail="User not found")
    
    u = user.data[0]
    parts = u.get("hashed_password", "").split("|")
    name = parts[1] if len(parts) > 1 else ""
    
    return {
        "id": u["id"],
        "email": u["email"],
        "name": name
    }
    
    return {
        "id": u["id"],
        "email": u["email"],
        "name": name
    }

# Register modular sub-routers
app.include_router(subjects_router)
app.include_router(sources_router)
app.include_router(books_router)
app.include_router(queries_router)
