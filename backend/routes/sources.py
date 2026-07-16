import os
import uuid
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, UploadFile, File
from fastapi.responses import FileResponse
from backend.config import supabase, chroma_client
from backend.auth import get_current_user
from backend.tasks import index_source_task

router = APIRouter(prefix="/subjects/{subject_id}/sources", tags=["sources"])

@router.post("")
async def upload_source(
    subject_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user)
):
    subject = supabase.table("subjects").select("*").eq("id", subject_id).eq("user_id", user_id).execute()
    if not subject.data:
        raise HTTPException(status_code=404, detail="Subject not found or access denied")
    
    collection_name = subject.data[0]["chroma_collection_name"]
    
    file_content = await file.read()
    file_ext = os.path.splitext(file.filename)[1].lower()
    
    if file_ext == ".pdf":
        source_type = "text_pdf"
    elif file_ext in [".png", ".jpg", ".jpeg", ".webp"]:
        source_type = "image_ocr"
    else:
        raise HTTPException(status_code=400, detail="Unsupported file format. Must be PDF or image.")
    
    try:
        from backend.config import upload_to_cloudinary
        storage_path = upload_to_cloudinary(file_content, file.filename, folder=f"{user_id}/{subject_id}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload to Cloudinary: {str(e)}")
        
    try:
        source_insert = supabase.table("sources").insert({
            "subject_id": subject_id,
            "source_type": source_type,
            "title": file.filename,
            "storage_path": storage_path
        }).execute()
        source_data = source_insert.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create database record: {str(e)}")
        
    background_tasks.add_task(
        index_source_task,
        source_data["id"],
        subject_id,
        file_content,
        file.filename,
        collection_name,
        source_type
    )
    
    return {
        "message": "Source uploaded successfully. Indexing is running in the background.",
        "source": source_data
    }

@router.delete("/{source_id}")
def delete_source(subject_id: str, source_id: str, user_id: str = Depends(get_current_user)):
    subject = supabase.table("subjects").select("*").eq("id", subject_id).eq("user_id", user_id).execute()
    if not subject.data:
        raise HTTPException(status_code=404, detail="Subject not found or access denied")
        
    source_res = supabase.table("sources").select("*").eq("id", source_id).eq("subject_id", subject_id).execute()
    if not source_res.data:
        raise HTTPException(status_code=404, detail="Source not found")
        
    source_data = source_res.data[0]
    storage_path = source_data.get("storage_path")
    
    try:
        supabase.table("sources").delete().eq("id", source_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete source record: {str(e)}")
        
    if storage_path:
        try:
            if storage_path.startswith("http"):
                from backend.config import delete_cloudinary_file
                delete_cloudinary_file(storage_path)
            else:
                supabase.storage.from_("user-uploads").remove(paths=[storage_path])
        except Exception as e:
            print(f"Failed to delete file from storage: {e}")
            
    collection_name = subject.data[0].get("chroma_collection_name")
    if collection_name:
        try:
            collection = chroma_client.get_collection(name=collection_name)
            collection.delete(where={"source_id": source_id})
        except Exception as e:
            print(f"Failed to delete vectors from Chroma DB: {e}")
            
    return {"message": "Source deleted successfully"}

@router.get("/{source_id}/content")
def get_source_content(
    subject_id: str,
    source_id: str,
    user_id: str = Depends(get_current_user)
):
    subject = supabase.table("subjects").select("*").eq("id", subject_id).eq("user_id", user_id).execute()
    if not subject.data:
        raise HTTPException(status_code=404, detail="Subject not found or access denied")
        
    source = supabase.table("sources").select("*").eq("id", source_id).eq("subject_id", subject_id).execute()
    if not source.data:
        raise HTTPException(status_code=404, detail="Source not found")
        
    storage_path = source.data[0]["storage_path"]
    
    try:
        from backend.config import download_file_bytes
        file_bytes = download_file_bytes(storage_path)
        text_content = file_bytes.decode("utf-8")
        return {"content": text_content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch content: {str(e)}")

@router.get("/{source_id}/file")
def get_source_file(
    subject_id: str,
    source_id: str,
    user_id: str = Depends(get_current_user)
):
    from fastapi.responses import StreamingResponse, Response
    import requests as _requests

    subject = supabase.table("subjects").select("*").eq("id", subject_id).eq("user_id", user_id).execute()
    if not subject.data:
        raise HTTPException(status_code=404, detail="Subject not found or access denied")
        
    source = supabase.table("sources").select("*").eq("id", source_id).eq("subject_id", subject_id).execute()
    if not source.data:
        raise HTTPException(status_code=404, detail="Source not found")
        
    storage_path = source.data[0]["storage_path"]
    source_title = source.data[0].get("title", "file")
    
    if storage_path.startswith("processing:") or storage_path.startswith("failed:"):
        raise HTTPException(status_code=202, detail="File is still being processed")
    
    # --- Determine content type from source type and title ---
    source_type = source.data[0].get("source_type", "")
    
    if source_type == "text_pdf" or source_title.lower().endswith(".pdf"):
        content_type = "application/pdf"
        file_ext = ".pdf"
    elif "png" in source_title.lower():
        content_type = "image/png"
        file_ext = ".png"
    elif "webp" in source_title.lower():
        content_type = "image/webp"
        file_ext = ".webp"
    else:
        content_type = "image/jpeg"
        file_ext = ".jpg"

    # --- Proxy the file from Cloudinary (or any HTTP URL) through our server ---
    # This avoids X-Frame-Options and CORS errors in the browser iframe
    if storage_path.startswith("http"):
        try:
            upstream = _requests.get(storage_path, timeout=60, stream=True)
            upstream.raise_for_status()
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Failed to fetch file from storage: {str(e)}")

        safe_filename = os.path.basename(source_title) or f"file{file_ext}"
        
        def iter_upstream():
            for chunk in upstream.iter_content(chunk_size=65536):
                if chunk:
                    yield chunk

        return StreamingResponse(
            iter_upstream(),
            media_type=content_type,
            headers={
                "Content-Disposition": f"inline; filename=\"{safe_filename}\"",
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "public, max-age=3600",
            }
        )

    # --- Legacy local file fallback ---
    file_ext_local = os.path.splitext(storage_path)[1].lower()
    local_cache_dir = "cache/uploads"
    os.makedirs(local_cache_dir, exist_ok=True)
    local_path = os.path.join(local_cache_dir, f"{source_id}{file_ext_local}")
    
    try:
        if not os.path.exists(local_path):
            from backend.config import download_file_bytes
            file_bytes = download_file_bytes(storage_path)
            with open(local_path, "wb") as f:
                f.write(file_bytes)
        return FileResponse(local_path, media_type=content_type)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch file: {str(e)}")

