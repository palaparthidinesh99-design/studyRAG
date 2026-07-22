import uuid
import time
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List

from backend.config import supabase, chroma_client
from backend.auth import get_current_user
from backend.models import CreateSubjectRequest, SaveNoteRequest
from backend.processors import split_into_subchunks

router = APIRouter(prefix="/subjects", tags=["subjects"])

from backend.db_helpers import _IN_MEMORY_SUBJECTS

@router.post("")
def create_subject(
    req: CreateSubjectRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user)
):
    subject_id = str(uuid.uuid4())
    collection_name = f"subject_{uuid.uuid4().hex}"
    created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ")

    # 1. Try public.users sync
    try:
        user_check = supabase.table("users").select("id").eq("id", user_id).execute()
        if not user_check.data:
            user_email = f"user_{user_id.replace('-', '')[:12]}@study.rag"
            try:
                auth_user = supabase.auth.admin.get_user_by_id(user_id)
                if auth_user and auth_user.user and auth_user.user.email:
                    user_email = auth_user.user.email
            except Exception:
                pass

            supabase.table("users").upsert({
                "id": user_id,
                "email": user_email,
                "hashed_password": "supabase_auth||true|"
            }).execute()
    except Exception as sync_e:
        print(f"Sync public.users notice: {sync_e}")

    # 2. Try Supabase subjects table insert
    created_subject = None
    try:
        result = supabase.table("subjects").insert({
            "id": subject_id,
            "user_id": user_id,
            "name": req.name,
            "chroma_collection_name": collection_name,
        }).execute()
        if result and result.data:
            created_subject = result.data[0]
    except Exception as e:
        print(f"Supabase subjects insert notice (using fallback): {e}")

    # 3. If Supabase RLS or DB error occurs, store in _IN_MEMORY_SUBJECTS fallback store
    if not created_subject:
        created_subject = {
            "id": subject_id,
            "user_id": user_id,
            "name": req.name,
            "chroma_collection_name": collection_name,
            "created_at": created_at
        }
        _IN_MEMORY_SUBJECTS[subject_id] = created_subject

    # Create Chroma collection in background
    if chroma_client is not None:
        try:
            background_tasks.add_task(chroma_client.get_or_create_collection, name=collection_name)
        except Exception as chroma_err:
            print(f"Chroma collection creation notice: {chroma_err}")

    return created_subject

@router.get("")
def list_subjects(user_id: str = Depends(get_current_user)):
    db_subjects = []
    try:
        result = supabase.table("subjects").select("*").eq("user_id", user_id).execute()
        if result and result.data:
            db_subjects = result.data
    except Exception as e:
        print(f"Supabase list_subjects notice: {e}")

    mem_subjects = [s for s in _IN_MEMORY_SUBJECTS.values() if s.get("user_id") == user_id]
    seen_ids = {s["id"] for s in db_subjects}
    for ms in mem_subjects:
        if ms["id"] not in seen_ids:
            db_subjects.append(ms)

    return db_subjects

@router.delete("/{subject_id}")
def delete_subject(subject_id: str, user_id: str = Depends(get_current_user)):
    subj_data = None
    try:
        subject = supabase.table("subjects").select("*").eq("id", subject_id).execute()
        if subject and subject.data:
            subj_data = subject.data[0]
    except Exception as e:
        print(f"Supabase delete_subject lookup notice: {e}")

    if not subj_data and subject_id in _IN_MEMORY_SUBJECTS:
        subj_data = _IN_MEMORY_SUBJECTS[subject_id]

    if not subj_data:
        raise HTTPException(status_code=404, detail="Subject not found or access denied")

    collection_name = subj_data.get("chroma_collection_name")
    _IN_MEMORY_SUBJECTS.pop(subject_id, None)

    try:
        supabase.table("subject_books").delete().eq("subject_id", subject_id).execute()
        supabase.table("sources").delete().eq("subject_id", subject_id).execute()
        supabase.table("queries").delete().eq("subject_id", subject_id).execute()
        supabase.table("subjects").delete().eq("id", subject_id).execute()
    except Exception as e:
        print(f"Supabase tables record delete notice: {e}")
        
    if collection_name:
        try:
            chroma_client.delete_collection(name=collection_name)
        except Exception as e:
            print(f"Failed to delete Chroma collection: {e}")
            
    return {"message": "Subject deleted successfully"}

@router.get("/{subject_id}/history")
def get_subject_history(
    subject_id: str,
    user_id: str = Depends(get_current_user)
):
    subject = supabase.table("subjects").select("*").eq("id", subject_id).eq("user_id", user_id).execute()
    if not subject.data:
        raise HTTPException(status_code=404, detail="Subject not found or access denied")
        
    try:
        result = supabase.table("queries").select("*").eq("subject_id", subject_id).order("created_at", desc=True).execute()
        return result.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch query history: {str(e)}")

@router.get("/{subject_id}/sources")
def list_subject_sources(
    subject_id: str,
    user_id: str = Depends(get_current_user)
):
    subject = supabase.table("subjects").select("*").eq("id", subject_id).eq("user_id", user_id).execute()
    if not subject.data:
        raise HTTPException(status_code=404, detail="Subject not found or access denied")
        
    try:
        result = supabase.table("sources").select("*").eq("subject_id", subject_id).execute()
        return result.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch sources: {str(e)}")

@router.get("/{subject_id}/books")
def list_subject_books(
    subject_id: str,
    user_id: str = Depends(get_current_user)
):
    subject = supabase.table("subjects").select("id").eq("id", subject_id).eq("user_id", user_id).execute()
    if not subject.data:
        raise HTTPException(status_code=404, detail="Subject not found or access denied")
        
    try:
        result = supabase.table("subject_books").select("global_books(id, title)").eq("subject_id", subject_id).execute()
        return [{"id": item["global_books"]["id"], "title": item["global_books"]["title"]} for item in result.data if item.get("global_books")]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch linked books: {str(e)}")

@router.get("/{subject_id}/data")
def get_subject_data(
    subject_id: str,
    user_id: str = Depends(get_current_user)
):
    subject = supabase.table("subjects").select("id").eq("id", subject_id).eq("user_id", user_id).execute()
    if not subject.data:
        raise HTTPException(status_code=404, detail="Subject not found or access denied")

    try:
        def fetch_sources():
            return supabase.table("sources").select("*").eq("subject_id", subject_id).execute().data
        
        def fetch_books():
            result = supabase.table("subject_books").select("global_books(id, title, chroma_collection_name)").eq("subject_id", subject_id).execute()
            books_to_check = [item["global_books"] for item in result.data if item.get("global_books")]
            return [
                {
                    "id": gb["id"],
                    "title": gb["title"],
                    "is_ready": True
                }
                for gb in books_to_check if gb
            ]
        
        def fetch_history():
            return supabase.table("queries").select("id,input_type,extracted_text,generated_answer,sections_used,created_at").eq("subject_id", subject_id).order("created_at", desc=True).limit(8).execute().data
        
        sources, books, history = [], [], []
        with ThreadPoolExecutor(max_workers=3) as executor:
            fs = {
                executor.submit(fetch_sources): "sources",
                executor.submit(fetch_books): "books",
                executor.submit(fetch_history): "history",
            }
            for future in as_completed(fs):
                key = fs[future]
                result = future.result()
                if key == "sources":
                    sources = result
                elif key == "books":
                    books = result
                elif key == "history":
                    history = result
        
        return {"sources": sources, "books": books, "history": history}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch subject data: {str(e)}")

@router.post("/{subject_id}/saved-notes")
def save_chat_note(
    subject_id: str,
    req: SaveNoteRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user)
):
    subject = supabase.table("subjects").select("*").eq("id", subject_id).eq("user_id", user_id).execute()
    if not subject.data:
        raise HTTPException(status_code=404, detail="Subject not found or access denied")
        
    collection_name = subject.data[0]["chroma_collection_name"]
    
    note_content_bytes = req.content.encode("utf-8")
    storage_path = f"{user_id}/{subject_id}/notes/{uuid.uuid4().hex}.md"
    try:
        supabase.storage.from_("user-uploads").upload(
            path=storage_path,
            file=note_content_bytes,
            file_options={"content-type": "text/markdown"}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload note to Storage: {str(e)}")
        
    try:
        source_insert = supabase.table("sources").insert({
            "subject_id": subject_id,
            "source_type": "saved_note",
            "title": req.title,
            "storage_path": storage_path
        }).execute()
        source_data = source_insert.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create source record: {str(e)}")

    return {"message": "Note saved successfully", "source": source_data}
