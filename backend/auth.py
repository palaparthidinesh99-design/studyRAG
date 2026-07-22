import os
from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional
from backend.config import supabase

security = HTTPBearer(auto_error=False)

def decode_access_token(token: str) -> Optional[str]:
    try:
        res = supabase.auth.get_user(token)
        if res and res.user:
            return res.user.id
    except Exception as e:
        print(f"Supabase auth token verification error: {e}")
    return None

def get_current_user(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)) -> str:
    token = request.query_params.get("token")
    if not token and credentials:
        token = credentials.credentials
        
    if not token:
        raise HTTPException(status_code=401, detail="Authentication token missing")
        
    user_id = decode_access_token(token)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
        
    return user_id

def get_current_user_details(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)) -> dict:
    token = request.query_params.get("token")
    if not token and credentials:
        token = credentials.credentials
        
    if not token:
        raise HTTPException(status_code=401, detail="Authentication token missing")
        
    try:
        res = supabase.auth.get_user(token)
        if res and res.user:
            u = res.user
            u_meta = u.user_metadata or {}
            name = u_meta.get("name") or u_meta.get("full_name") or ""
            if not name and u.email:
                name = u.email.split("@")[0].capitalize()
            if not name:
                name = "Student"
            return {
                "id": u.id,
                "email": u.email or "",
                "name": name
            }
    except Exception as e:
        print(f"Supabase auth token verification error: {e}")

    raise HTTPException(status_code=401, detail="Invalid or expired token")
