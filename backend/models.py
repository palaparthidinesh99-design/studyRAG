from pydantic import BaseModel
from typing import Optional

class RegisterRequest(BaseModel):
    email: str
    password: str
    name: Optional[str] = None

class LoginRequest(BaseModel):
    email: str
    password: str

class CreateSubjectRequest(BaseModel):
    name: str

class QueryTextRequest(BaseModel):
    query: str
    source_filter: Optional[str] = "all"
    query_id: Optional[str] = None

class SaveNoteRequest(BaseModel):
    title: str
    content: str

class LinkCatalogueBookRequest(BaseModel):
    source_id: str
    title: str
    pdf_url: str
    source: str
