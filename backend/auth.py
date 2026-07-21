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
