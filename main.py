import os
import random
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from backend.config import supabase
from backend.models import RegisterRequest, LoginRequest, VerifyEmailRequest, ResendCodeRequest
from backend.auth import get_current_user

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
    try:
        auth_res = supabase.auth.sign_up({
            "email": req.email,
            "password": req.password,
            "options": {
                "data": {"name": req.name or ""}
            }
        })
    except Exception as auth_err:
        err_msg = str(auth_err)
        if "already registered" in err_msg.lower() or "already exists" in err_msg.lower():
            raise HTTPException(status_code=400, detail="Email already registered")
        raise HTTPException(status_code=400, detail=f"Registration failed: {err_msg}")

    if not auth_res or not auth_res.user:
        raise HTTPException(status_code=400, detail="Failed to register user with Supabase Auth.")

    user_id = auth_res.user.id
    access_token = auth_res.session.access_token if auth_res.session else None

    try:
        db_field = f"supabase_auth|{req.name or ''}|true|"
        supabase.table("users").insert({
            "id": user_id,
            "email": req.email,
            "hashed_password": db_field
        }).execute()
    except Exception as e:
        print(f"User sync to public.users table: {e}")

    if not access_token:
        try:
            login_res = supabase.auth.sign_in_with_password({
                "email": req.email,
                "password": req.password
            })
            if login_res and login_res.session:
                access_token = login_res.session.access_token
        except Exception:
            pass

    return {"access_token": access_token or "", "token_type": "bearer", "status": "verified"}

@app.post("/verify-email")
def verify_email(req: VerifyEmailRequest):
    return {"status": "ok", "message": "Email is already verified."}

@app.post("/resend-code")
def resend_code(req: ResendCodeRequest):
    return {"status": "ok", "message": "Email is already verified."}

@app.post("/login")
def login(req: LoginRequest):
    try:
        auth_res = supabase.auth.sign_in_with_password({
            "email": req.email,
            "password": req.password
        })
        if not auth_res or not auth_res.session:
            raise HTTPException(status_code=401, detail="Invalid email or password")
        return {"access_token": auth_res.session.access_token, "token_type": "bearer"}
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid email or password")

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
