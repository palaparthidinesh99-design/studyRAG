import os
import requests
import io
from PIL import Image
from fastapi import HTTPException
from typing import List, Optional
from backend.config import GROQ_API_KEY, OLLAMA_URL, OLLAMA_API_KEY

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY", "")

def call_gemini_embeddings(texts: List[str]) -> Optional[List[List[float]]]:
    """Generate high-quality 768-dimension vector embeddings using Google Gemini API.
    
    Returns a list of embedding vectors (floats), or None if the API key is not configured or fails.
    """
    if not GOOGLE_API_KEY:
        print("Warning: GOOGLE_API_KEY is not configured. Falling back to default Chroma embedding function.")
        return None
        
    try:
        embeddings = []
        # Batch requests to Google API (limit 100 content elements per batch)
        batch_size = 100
        for i in range(0, len(texts), batch_size):
            batch_texts = texts[i:i+batch_size]
            requests_payload = []
            for t in batch_texts:
                # Sanitize text
                t_clean = t.strip() if t else "empty"
                if not t_clean:
                    t_clean = "empty"
                requests_payload.append({
                    "model": "models/text-embedding-004",
                    "content": {
                        "parts": [{"text": t_clean}]
                    }
                })
                
            url = f"https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:batchEmbedContents?key={GOOGLE_API_KEY}"
            res = requests.post(url, json={"requests": requests_payload}, timeout=30)
            res.raise_for_status()
            res_data = res.json()
            
            # Extract embeddings
            for emb_obj in res_data.get("embeddings", []):
                embeddings.append(emb_obj.get("values", []))
                
        if len(embeddings) == len(texts):
            return embeddings
        else:
            print(f"Warning: Gemini embedding count mismatch: expected {len(texts)}, got {len(embeddings)}")
            return None
    except Exception as e:
        print(f"Warning: Gemini embedding generation failed: {e}. Falling back to default Chroma embedding function.")
        return None

def call_gemini(messages: List[dict], model: str = "gemini-2.0-flash", max_tokens: int = 8192) -> str:
    """Call Google Gemini API for fast, high-quality LLM generation."""
    if not GOOGLE_API_KEY:
        raise Exception("GOOGLE_API_KEY not configured")
    
    # Convert OpenAI messages format to Gemini format
    contents = []
    system_text = ""
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content", "")
        if role == "system":
            system_text = content
        elif role == "user":
            # Prepend system text to first user message if present
            if system_text:
                content = f"{system_text}\n\n{content}"
                system_text = ""
            contents.append({"role": "user", "parts": [{"text": content}]})
        elif role == "assistant":
            contents.append({"role": "model", "parts": [{"text": content}]})
    
    # Append remaining system text if no user message followed
    if system_text and contents:
        contents[0]["parts"][0]["text"] = system_text + "\n\n" + contents[0]["parts"][0]["text"]
    elif system_text:
        contents.append({"role": "user", "parts": [{"text": system_text}]})
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GOOGLE_API_KEY}"
    payload = {
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": 0.2
        }
    }
    res = requests.post(url, json=payload, timeout=120)
    res.raise_for_status()
    data = res.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise Exception(f"Unexpected Gemini response: {data}")

def call_ollama(endpoint: str, payload: dict) -> requests.Response:
    url = f"{OLLAMA_URL}/{endpoint}"
    headers = {}
    if OLLAMA_API_KEY:
        headers["Authorization"] = f"Bearer {OLLAMA_API_KEY}"
    res = requests.post(url, json=payload, headers=headers)
    return res

def call_ollama_fallback(messages: List[dict], max_tokens: int = 4096) -> str:
    payload = {
        "model": "gpt-oss:20b",
        "messages": messages,
        "stream": False,
        "options": {
            "num_ctx": 16384,
            "num_predict": max_tokens
        }
    }
    try:
        res = call_ollama("chat", payload)
        res_json = res.json()
        if "message" in res_json and "content" in res_json["message"]:
            return res_json["message"]["content"]
        elif "response" in res_json:
            return res_json["response"]
        else:
            raise Exception(f"Unexpected response structure: {res_json}")
    except Exception as e:
        print(f"Ollama fallback failed: {e}")
        raise e

def call_groq(messages: List[dict], model: str = "llama3-8b-8192", max_tokens: int = 4096, temperature: float = 0.2, timeout: int = 6) -> str:

    if not GROQ_API_KEY:
        raise HTTPException(status_code=500, detail="GROQ_API_KEY not configured. Please set it in your environment.")
    
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens
    }
    
    # Truncate any huge messages to avoid 413 errors
    for msg in payload["messages"]:
        if isinstance(msg.get("content"), str) and len(msg["content"]) > 12000:
            msg["content"] = msg["content"][:12000] + "\n...[truncated]"
    
    FALLBACK_MODELS = ["llama-3.3-70b-versatile", "llama3-8b-8192", "mixtral-8x7b-32768"]
    
    # Try requested model first
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=timeout)
        res.raise_for_status()
        return res.json()["choices"][0]["message"]["content"]
    except Exception as primary_err:
        print(f"Groq model '{model}' failed: {primary_err}")
    
    # Try fallbacks in order, skipping the model that just failed
    for fallback_model in FALLBACK_MODELS:
        if fallback_model == model:
            continue
        try:
            payload["model"] = fallback_model
            if payload.get("max_tokens", 0) > 4000:
                payload["max_tokens"] = 4000
            res = requests.post(url, json=payload, headers=headers, timeout=timeout)
            res.raise_for_status()
            return res.json()["choices"][0]["message"]["content"]
        except Exception as fb_err:
            print(f"Groq fallback '{fallback_model}' failed: {fb_err}")
    
    raise HTTPException(status_code=503, detail="All Groq models failed. Please try again in a moment.")

def call_groq_vision(prompt: str, image_b64: str) -> str:
    if not GROQ_API_KEY:
        raise HTTPException(status_code=500, detail="GROQ_API_KEY not configured.")

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # Use Groq's active vision models (llama-3.2-11b-vision-preview)
    VISION_MODELS = [
        "llama-3.2-11b-vision-preview",
        "llama-3.2-90b-vision-preview"
    ]
    
    for vision_model in VISION_MODELS:
        payload = {
            "model": vision_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}
                        }
                    ]
                }
            ],
            "temperature": 0.2,
            "max_tokens": 4096
        }
        try:
            res = requests.post(url, json=payload, headers=headers, timeout=45)
            res.raise_for_status()
            return res.json()["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"Groq Vision model '{vision_model}' failed: {e}")
    
    raise HTTPException(status_code=500, detail="All Groq vision models failed. Please try a smaller or clearer image.")


def compress_image(image_bytes: bytes, max_size: int = 1024) -> bytes:
    try:
        img = Image.open(io.BytesIO(image_bytes))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        
        width, height = img.size
        if width > max_size or height > max_size:
            if width > height:
                new_width = max_size
                new_height = int(height * (max_size / width))
            else:
                new_height = max_size
                new_width = int(width * (max_size / height))
            img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
            
        out_buf = io.BytesIO()
        img.save(out_buf, format="JPEG", quality=75)
        return out_buf.getvalue()
    except Exception as e:
        print(f"Image compression failed: {e}")
        return image_bytes

def is_pdf_valid(path: str) -> bool:
    if not os.path.exists(path):
        return False
    if os.path.getsize(path) < 1024:
        try:
            os.remove(path)
        except Exception:
            pass
        return False
    if path.endswith(".txt"):
        return True
    try:
        from pypdf import PdfReader
        with open(path, "rb") as f:
            reader = PdfReader(f)
            _ = len(reader.pages)
        return True
    except Exception:
        try:
            os.remove(path)
        except Exception:
            pass
        return False
