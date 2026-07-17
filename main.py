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
    existing = supabase.table("users").select("*").eq("email", req.email).execute()
    if existing.data:
        # Check if the existing user is unverified. If so, allow re-registration by updating code
        user = existing.data[0]
        db_hashed = user["hashed_password"]
        parts = db_hashed.split("|")
        verified = parts[2] if len(parts) > 2 else "true"
        if verified == "false":
            hashed = hash_password(req.password)
            code = str(random.randint(100000, 999999))
            db_password_field = f"{hashed}|{req.name or ''}|false|{code}"
            supabase.table("users").update({"hashed_password": db_password_field}).eq("id", user["id"]).execute()
            
            print("=" * 60)
            print(f"EMAIL VERIFICATION CODE (RE-REGISTER) FOR {req.email}: {code}")
            print("=" * 60)
            return {"status": "verification_pending", "email": req.email, "message": "Email verification pending. Verification code printed to server logs."}
        raise HTTPException(status_code=400, detail="Email already registered")

    hashed = hash_password(req.password)
    code = str(random.randint(100000, 999999))
    # Store: hashed_password|name|is_verified|verification_code
    db_password_field = f"{hashed}|{req.name or ''}|false|{code}"
    
    try:
        result = supabase.table("users").insert({
            "email": req.email,
            "hashed_password": db_password_field
        }).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create user: {str(e)}")

    print("=" * 60)
    print(f"EMAIL VERIFICATION CODE FOR {req.email}: {code}")
    print("=" * 60)
    return {"status": "verification_pending", "email": req.email, "message": "Verification code printed to server logs."}

@app.post("/verify-email")
def verify_email(req: VerifyEmailRequest):
    result = supabase.table("users").select("*").eq("email", req.email).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="User not found")

    user = result.data[0]
    db_hashed = user["hashed_password"]
    parts = db_hashed.split("|")
    hashed = parts[0]
    name = parts[1] if len(parts) > 1 else ""
    verified = parts[2] if len(parts) > 2 else "true"
    db_code = parts[3] if len(parts) > 3 else ""

    if verified == "true":
        token = create_access_token(user["id"])
        return {"access_token": token, "token_type": "bearer", "status": "already_verified"}

    if req.code != db_code:
        raise HTTPException(status_code=400, detail="Invalid verification code")

    # Update verification status
    updated_field = f"{hashed}|{name}|true|"
    supabase.table("users").update({"hashed_password": updated_field}).eq("id", user["id"]).execute()

    token = create_access_token(user["id"])
    return {"access_token": token, "token_type": "bearer", "status": "verified"}

@app.post("/resend-code")
def resend_code(req: ResendCodeRequest):
    result = supabase.table("users").select("*").eq("email", req.email).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="User not found")

    user = result.data[0]
    db_hashed = user["hashed_password"]
    parts = db_hashed.split("|")
    hashed = parts[0]
    name = parts[1] if len(parts) > 1 else ""
    verified = parts[2] if len(parts) > 2 else "true"

    if verified == "true":
        return {"status": "already_verified", "message": "Email is already verified."}

    code = str(random.randint(100000, 999999))
    updated_field = f"{hashed}|{name}|false|{code}"
    supabase.table("users").update({"hashed_password": updated_field}).eq("id", user["id"]).execute()

    print("=" * 60)
    print(f"RESENT EMAIL VERIFICATION CODE FOR {req.email}: {code}")
    print("=" * 60)
    return {"status": "ok", "message": "Verification code resent."}

@app.post("/login")
def login(req: LoginRequest):
    result = supabase.table("users").select("*").eq("email", req.email).execute()
    if not result.data:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    user = result.data[0]
    db_hashed = user["hashed_password"]
    parts = db_hashed.split("|")
    hashed_password = parts[0]
    verified = parts[2] if len(parts) > 2 else "true"

    if not verify_password(req.password, hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if verified == "false":
        raise HTTPException(
            status_code=401, 
            detail="Email verification pending. Please verify your email first."
        )

    token = create_access_token(user["id"])
    return {"access_token": token, "token_type": "bearer"}

@app.get("/me")
def read_current_user(user_id: str = Depends(get_current_user)):
    user = supabase.table("users").select("id", "email", "hashed_password").eq("id", user_id).execute()
    if not user.data:
        raise HTTPException(status_code=404, detail="User not found")
    
    u = user.data[0]
    parts = u.get("hashed_password", "").split("|")
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
