import os
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from backend.config import supabase
from backend.models import RegisterRequest, LoginRequest
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

# Authentication Endpoints
@app.post("/register")
def register(req: RegisterRequest):
    existing = supabase.table("users").select("*").eq("email", req.email).execute()
    if existing.data:
        raise HTTPException(status_code=400, detail="Email already registered")

    hashed = hash_password(req.password)
    # Store the user's name appended to their password hash
    db_password_field = f"{hashed}|{req.name}" if req.name else hashed
    
    result = supabase.table("users").insert({
        "email": req.email,
        "hashed_password": db_password_field
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
    db_hashed = user["hashed_password"]
    parts = db_hashed.split("|", 1)
    hashed_password = parts[0]
    
    if not verify_password(req.password, hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(user["id"])
    return {"access_token": token, "token_type": "bearer"}

@app.get("/me")
def read_current_user(user_id: str = Depends(get_current_user)):
    user = supabase.table("users").select("id", "email", "hashed_password").eq("id", user_id).execute()
    if not user.data:
        raise HTTPException(status_code=404, detail="User not found")
    
    u = user.data[0]
    parts = u.get("hashed_password", "").split("|", 1)
    name = parts[1] if len(parts) > 1 else ""
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
