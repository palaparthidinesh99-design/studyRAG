import uuid
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List

from backend.config import supabase, chroma_client
from backend.auth import get_current_user
from backend.models import CreateSubjectRequest, SaveNoteRequest
from backend.processors import split_into_subchunks

router = APIRouter(prefix="/subjects", tags=["subjects"])

@router.post("")
def create_subject(
    req: CreateSubjectRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user)
):
    collection_name = f"subject_{uuid.uuid4().hex}"
    
    # Ensure public.users table contains a row for user_id to satisfy foreign key constraint
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
        print(f"Sync public.users on subject creation: {sync_e}")

    try:
        result = supabase.table("subjects").insert({
            "user_id": user_id,
            "name": req.name,
            "chroma_collection_name": collection_name,
        }).execute()
        
        if chroma_client is not None:
            try:
                from backend.config import NOOP_EF
                background_tasks.add_task(chroma_client.get_or_create_collection, name=collection_name, embedding_function=NOOP_EF)
            except Exception as chroma_err:
                print(f"Chroma collection creation warning: {chroma_err}")

        if result and result.data:
            return result.data[0]
        return {"id": collection_name, "user_id": user_id, "name": req.name, "chroma_collection_name": collection_name}
    except Exception as e:
        err_str = str(e)
        print(f"Failed to create subject in database: {err_str}")
        if "row-level security policy" in err_str.lower() or "42501" in err_str:
            raise HTTPException(
                status_code=400,
                detail="Database Error (RLS 42501): Row-Level Security policy on 'subjects' table is blocking inserts. Please add SUPABASE_SERVICE_ROLE_KEY to environment variables or disable RLS for 'subjects' in Supabase SQL editor: ALTER TABLE subjects DISABLE ROW LEVEL SECURITY;"
            )
        raise HTTPException(status_code=500, detail=f"Failed to create subject: {err_str}")

@router.get("")
def list_subjects(user_id: str = Depends(get_current_user)):
    result = supabase.table("subjects").select("*").eq("user_id", user_id).execute()
    return result.data

@router.delete("/{subject_id}")
def delete_subject(subject_id: str, user_id: str = Depends(get_current_user)):
    subject = supabase.table("subjects").select("*").eq("id", subject_id).eq("user_id", user_id).execute()
    if not subject.data:
        raise HTTPException(status_code=404, detail="Subject not found or access denied")
        
    subj_data = subject.data[0]
    collection_name = subj_data.get("chroma_collection_name")
    
    try:
        supabase.table("subject_books").delete().eq("subject_id", subject_id).execute()
        supabase.table("sources").delete().eq("subject_id", subject_id).execute()
        supabase.table("queries").delete().eq("subject_id", subject_id).execute()
        supabase.table("subjects").delete().eq("id", subject_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete subject records: {str(e)}")
        
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
