import os
import random
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from backend.config import supabase, supabase_admin
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
    user_id = None
    access_token = None

    # Try admin user creation first for instant verified accounts bypassing signup rate limits
    try:
        admin_res = supabase_admin.auth.admin.create_user({
            "email": req.email,
            "password": req.password,
            "email_confirm": True,
            "user_metadata": {"name": req.name or ""}
        })
        if admin_res and admin_res.user:
            user_id = admin_res.user.id
            login_res = supabase.auth.sign_in_with_password({
                "email": req.email,
                "password": req.password
            })
            if login_res and login_res.session:
                access_token = login_res.session.access_token
    except Exception as admin_err:
        err_msg = str(admin_err)
        if "already registered" in err_msg.lower() or "already exists" in err_msg.lower():
            raise HTTPException(status_code=400, detail="Email already registered")
        
        # Fallback to standard sign_up
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
                if auth_res.session:
                    access_token = auth_res.session.access_token
        except Exception as signup_err:
            signup_msg = str(signup_err)
            if "already registered" in signup_msg.lower() or "already exists" in signup_msg.lower():
                raise HTTPException(status_code=400, detail="Email already registered")
            raise HTTPException(status_code=400, detail=f"Registration failed: {signup_msg}")

    if not user_id:
        raise HTTPException(status_code=400, detail="Failed to register user with Supabase Auth.")

    try:
        db_field = f"supabase_auth|{req.name or ''}|true|"
        supabase_admin.table("users").upsert({
            "id": user_id,
            "email": req.email,
            "hashed_password": db_field
        }).execute()
    except Exception as e:
        print(f"User sync to public.users table: {e}")

    try:
        supabase.auth.admin.update_user_by_id(user_id, {"email_confirm": True})
    except Exception:
        pass

    if not access_token:
        try:
            login_res = supabase.auth.sign_in_with_password({
                "email": req.email,
                "password": req.password
            })
            if login_res and login_res.session:
                access_token = login_res.session.access_token
        except Exception as login_err:
            print(f"Post-register auto-login attempt: {login_err}")

    if not access_token:
        raise HTTPException(
            status_code=400,
            detail="Registration succeeded, but session could not be retrieved. Please log in."
        )

    return {"access_token": access_token, "token_type": "bearer", "status": "verified"}

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
        if not auth_res or not auth_res.session or not auth_res.user:
            raise HTTPException(status_code=401, detail="Invalid email or password")
            
        try:
            supabase_admin.table("users").upsert({
                "id": auth_res.user.id,
                "email": req.email,
                "hashed_password": "supabase_auth||true|"
            }).execute()
        except Exception as sync_e:
            print(f"Sync public.users on login: {sync_e}")

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
        db_pwd = u.get("hashed_password", "")
        if "|" in db_pwd:
            parts = db_pwd.split("|")
            if len(parts) > 1 and parts[1] and parts[1].strip() not in ["true", "false", "|true|", "supabase_auth"]:
                user_name = parts[1].strip()

    if not user_name or user_name in ["true", "false", "|true|", "none", "null"]:
        user_name = "Student"

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
