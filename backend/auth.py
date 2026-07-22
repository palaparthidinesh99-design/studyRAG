import os
import base64
import json
from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional
from backend.config import supabase, supabase_admin

security = HTTPBearer(auto_error=False)

def decode_access_token(token: str) -> Optional[str]:
    # 1. Fast, non-mutating JWT payload decoding to extract user ID without modifying Supabase client headers
    try:
        parts = token.split(".")
        if len(parts) >= 2:
            payload_b64 = parts[1]
            payload_b64 += "=" * (-len(payload_b64) % 4)
            data = json.loads(base64.urlsafe_b64decode(payload_b64))
            sub = data.get("sub")
            if sub:
                return sub
    except Exception as parse_e:
        print(f"JWT payload parse error: {parse_e}")

    # 2. Fallback to supabase_admin.auth.get_user(token)
    try:
        res = supabase_admin.auth.get_user(token)
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
