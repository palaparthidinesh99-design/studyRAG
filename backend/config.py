import os
import time
from dotenv import load_dotenv
from supabase import create_client, ClientOptions
import chromadb

# Ensure cache directory exists
os.makedirs("cache", exist_ok=True)

load_dotenv()

# JWT Config
JWT_SECRET = os.environ.get("JWT_SECRET", "default_secret_key_change_me_in_prod")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days

# Supabase Initialization
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", SUPABASE_KEY)

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set in environment")

# Primary DB client uses service role key if available to bypass RLS policy checks
db_key = SUPABASE_SERVICE_ROLE_KEY if SUPABASE_SERVICE_ROLE_KEY else SUPABASE_KEY

supabase = create_client(
    SUPABASE_URL,
    db_key,
    options=ClientOptions(
        storage_client_timeout=180,
        postgrest_client_timeout=120
    )
)

supabase_admin = create_client(
    SUPABASE_URL,
    SUPABASE_SERVICE_ROLE_KEY,
    options=ClientOptions(
        storage_client_timeout=180,
        postgrest_client_timeout=120
    )
)

# Chroma DB Initialization
CHROMA_API_KEY = os.environ.get("CHROMA_API_KEY", "").strip("'\"")
CHROMA_TENANT = os.environ.get("CHROMA_TENANT", "").strip("'\"")
CHROMA_DATABASE = os.environ.get("CHROMA_DATABASE", "studyRag").strip("'\"")

chroma_client = None
if CHROMA_API_KEY:
    print("Connecting to Chroma Cloud...")
    for attempt in range(3):
        try:
            chroma_client = chromadb.CloudClient(
                api_key=CHROMA_API_KEY,
                tenant=CHROMA_TENANT,
                database=CHROMA_DATABASE,
            )
            break
        except Exception as e:
            print(f"Warning: Attempt {attempt+1} failed to initialize Chroma Cloud client: {e}.")
            time.sleep(1.5)
else:
    print("Connecting to local Chroma PersistentClient...")
    try:
        chroma_client = chromadb.PersistentClient(path="cache/chroma")
    except Exception as local_e:
        print(f"Error: Failed to initialize local Chroma client: {local_e}")

OLLAMA_URL = os.environ.get("OLLAMA_URL", "https://ollama.com/api")
OLLAMA_API_KEY = os.environ.get("OLLAMA_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
CEREBRAS_API_KEY = os.environ.get("CEREBRAS_API_KEY")

# Cloudinary Configuration
import cloudinary
import cloudinary.uploader
import requests
import io

CLOUDINARY_CLOUD_NAME = os.environ.get("CLOUDINARY_CLOUD_NAME", "njmzwemg").strip("'\"")
CLOUDINARY_API_KEY = os.environ.get("CLOUDINARY_API_KEY", "113446517348587").strip("'\"")
CLOUDINARY_API_SECRET = os.environ.get("CLOUDINARY_API_SECRET", "kpQBvoYMai2Fz1HaSyVlJu1XZvI").strip("'\"")

cloudinary.config(
    cloud_name=CLOUDINARY_CLOUD_NAME,
    api_key=CLOUDINARY_API_KEY,
    api_secret=CLOUDINARY_API_SECRET,
    secure=True
)

def upload_to_cloudinary(file_bytes: bytes, file_name: str, folder: str = "") -> str:
    ext = os.path.splitext(file_name)[1].lower()
    is_image = ext in [".png", ".jpg", ".jpeg", ".webp"]
    is_pdf = ext == ".pdf"
    
    if is_image:
        try:
            from PIL import Image
            out = io.BytesIO()
            img = Image.open(io.BytesIO(file_bytes))
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img.save(out, format="JPEG", quality=75, optimize=True)
            file_content = out.getvalue()
            file_name_to_use = os.path.splitext(file_name)[0] + ".jpg"
        except Exception as e:
            print(f"PIL Image compression failed: {e}. Uploading original bytes.")
            file_content = file_bytes
            file_name_to_use = file_name
        resource_type = "image"
    elif is_pdf:
        file_content = file_bytes
        file_name_to_use = file_name
        resource_type = "raw"  # PDFs MUST use 'raw', not 'auto'
    else:
        file_content = file_bytes
        file_name_to_use = file_name
        resource_type = "raw"

    # Use a unique public_id to avoid conflicts between different users
    safe_name = os.path.splitext(file_name_to_use)[0].replace(" ", "_")
    import uuid as _uuid
    unique_public_id = f"{safe_name}_{_uuid.uuid4().hex[:8]}"

    res = cloudinary.uploader.upload(
        io.BytesIO(file_content),
        public_id=unique_public_id,
        folder=folder,
        resource_type=resource_type,
        overwrite=False,  # Never overwrite — each upload gets a unique ID
        access_mode="public",  # Ensure URL is publicly accessible without auth
        type="upload"
    )
    return res.get("secure_url")


def delete_cloudinary_file(url: str):
    try:
        if "res.cloudinary.com" not in url:
            return
        
        parts = url.split("/upload/")
        if len(parts) > 1:
            subparts = parts[1].split("/", 1)
            path_with_ext = subparts[1] if subparts[0].startswith("v") and len(subparts) > 1 else parts[1]
            public_id, _ = os.path.splitext(path_with_ext)
            
            resource_type = "image"
            if ".pdf" in url.lower():
                resource_type = "raw"
                
            cloudinary.uploader.destroy(public_id, resource_type=resource_type)
    except Exception as e:
        print(f"Failed to delete Cloudinary file: {e}")

def download_file_bytes(storage_path: str) -> bytes:
    if storage_path.startswith("http://") or storage_path.startswith("https://"):
        res = requests.get(storage_path, timeout=60)
        res.raise_for_status()
        return res.content
    else:
        return supabase.storage.from_("user-uploads").download(storage_path)
