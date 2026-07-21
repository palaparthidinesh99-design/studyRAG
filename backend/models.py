from pydantic import BaseModel
from typing import Optional

class RegisterRequest(BaseModel):
    email: str
    password: str
    name: Optional[str] = None

class LoginRequest(BaseModel):
    email: str
    password: str

class VerifyEmailRequest(BaseModel):
    email: str
    code: str

class ResendCodeRequest(BaseModel):
    email: str

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

class TriggerNotesRequest(BaseModel):
    source_id: str
    topics: list[str]
    pre_extracted_text: Optional[str] = ""
    custom_title: Optional[str] = None
