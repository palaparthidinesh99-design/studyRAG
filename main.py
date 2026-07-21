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

# Authentication Endpoints
@app.post("/register")
def register(req: RegisterRequest):
    user_id = None
    access_token = None
    try:
        auth_res = supabase.auth.sign_up({
            "email": req.email,
            "password": req.password,
            "options": {
                "data": {"name": req.name or ""}
            }
        })
        if auth_res and auth_res.user:
            user_id = auth_res.user.id
        if auth_res and auth_res.session:
            access_token = auth_res.session.access_token
    except Exception as auth_err:
        print(f"Supabase Auth sign_up error: {auth_err}")

    hashed = hash_password(req.password)
    db_password_field = f"{hashed}|{req.name or ''}|true|"
    
    existing = supabase.table("users").select("*").eq("email", req.email).execute()
    if existing.data:
        if not access_token and not user_id:
            raise HTTPException(status_code=400, detail="Email already registered")
        db_user = existing.data[0]
        user_id = db_user["id"]
    else:
        user_data = {
            "email": req.email,
            "hashed_password": db_password_field
        }
        if user_id:
            user_data["id"] = user_id
            
        try:
            result = supabase.table("users").insert(user_data).execute()
            if result.data:
                user_id = result.data[0]["id"]
        except Exception as e:
            if not user_id:
                raise HTTPException(status_code=500, detail=f"Failed to create user record: {str(e)}")

    if not access_token:
        access_token = create_access_token(user_id)
        
    return {"access_token": access_token, "token_type": "bearer", "status": "verified"}

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
    access_token = None
    user_id = None
    
    try:
        auth_res = supabase.auth.sign_in_with_password({
            "email": req.email,
            "password": req.password
        })
        if auth_res and auth_res.session:
            access_token = auth_res.session.access_token
            user_id = auth_res.user.id
    except Exception as auth_err:
        print(f"Supabase Auth sign_in error: {auth_err}")

    if not access_token:
        result = supabase.table("users").select("*").eq("email", req.email).execute()
        if not result.data:
            raise HTTPException(status_code=401, detail="Invalid email or password")

        user = result.data[0]
        db_hashed = user["hashed_password"]
        parts = db_hashed.split("|")
        hashed_password = parts[0]

        if not verify_password(req.password, hashed_password):
            raise HTTPException(status_code=401, detail="Invalid email or password")

        access_token = create_access_token(user["id"])

    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/me")
def read_current_user(user_id: str = Depends(get_current_user)):
    user_email = ""
    user_name = ""
    
    user = supabase.table("users").select("id", "email", "hashed_password").eq("id", user_id).execute()
    if user.data:
        u = user.data[0]
        user_email = u.get("email", "")
        parts = u.get("hashed_password", "").split("|")
        user_name = parts[1] if len(parts) > 1 else ""

    return {
        "id": user_id,
        "email": user_email,
        "name": user_name
    }

# Register modular sub-routers
app.include_router(subjects_router)
app.include_router(sources_router)
app.include_router(books_router)
app.include_router(queries_router)
