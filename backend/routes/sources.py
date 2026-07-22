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
        try:
            from backend.llm import compress_image
            file_content = compress_image(file_content)
        except Exception as compress_err:
            print(f"Failed to compress note image: {compress_err}")
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
        raise HTTPException(status_code=500, detail=f"Failed to create database record: {str(e)}") from e
    
    # Free the large file bytes from memory immediately — background task fetches from URL directly
    del file_content       
    # Pass Cloudinary URL (not raw bytes) to background task — prevents holding megabytes of binary data in RAM
    background_tasks.add_task(
        index_source_task,
        source_data["id"],
        subject_id,
        storage_path,  # Cloudinary URL — task will download it when it runs
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
            from backend.config import NOOP_EF
            collection = chroma_client.get_collection(name=collection_name, embedding_function=NOOP_EF)
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
    from fastapi.responses import RedirectResponse

    subject = supabase.table("subjects").select("*").eq("id", subject_id).eq("user_id", user_id).execute()
    if not subject.data:
        raise HTTPException(status_code=404, detail="Subject not found or access denied")
        
    source = supabase.table("sources").select("*").eq("id", source_id).eq("subject_id", subject_id).execute()
    if not source.data:
        raise HTTPException(status_code=404, detail="Source not found")
        
    storage_path = source.data[0]["storage_path"]
    
    if storage_path.startswith("processing:") or storage_path.startswith("failed:"):
        raise HTTPException(status_code=202, detail="File is still being processed")
    
    # 1. Direct Cloudinary or HTTP redirect
    if storage_path.startswith("http"):
        return RedirectResponse(url=storage_path)

    # 2. Supabase Storage: redirect to a secure signed URL
    try:
        res = supabase.storage.from_("user-uploads").create_signed_url(storage_path, expires_in=3600)
        signed_url = res.get("signedURL") or res.get("signed_url")
        if signed_url:
            return RedirectResponse(url=signed_url)
            
        pub_url = supabase.storage.from_("user-uploads").get_public_url(storage_path)
        return RedirectResponse(url=pub_url)
    except Exception as e:
        print(f"Failed to generate signed redirect URL: {e}")
        from backend.config import SUPABASE_URL
        fallback_url = f"{SUPABASE_URL}/storage/v1/object/public/user-uploads/{storage_path}"
        return RedirectResponse(url=fallback_url)

