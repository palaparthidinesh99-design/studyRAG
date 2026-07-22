import os
import requests
import io
from PIL import Image
from fastapi import HTTPException
from typing import List, Optional
from backend.config import GROQ_API_KEY, OLLAMA_URL, OLLAMA_API_KEY

raw_keys = []
for env_name in ["GOOGLE_API_KEY", "GOOGLE_API_KEY_2", "GOOGLE_API_KEY_3", "GEMINI_API_KEY"]:
    val = os.environ.get(env_name, "")
    if val:
        raw_keys.extend([k.strip() for k in val.split(",") if k.strip()])

GOOGLE_API_KEYS = list(dict.fromkeys(raw_keys))
_KEY_INDEX = 0

def get_current_gemini_key() -> str:
    global _KEY_INDEX
    if not GOOGLE_API_KEYS:
        return ""
    key = GOOGLE_API_KEYS[_KEY_INDEX % len(GOOGLE_API_KEYS)]
    return key

def rotate_gemini_key():
    global _KEY_INDEX
    if GOOGLE_API_KEYS:
        _KEY_INDEX = (_KEY_INDEX + 1) % len(GOOGLE_API_KEYS)

import time
import hashlib
import numpy as np

def generate_fallback_embedding(text: str, dim: int = 384) -> List[float]:
    """Deterministic, fast fallback vector embedding generator when API keys are rate limited."""
    tokens = text.lower().split()
    vec = np.zeros(dim, dtype=np.float32)
    for tok in tokens:
        h = hashlib.sha256(tok.encode("utf-8")).digest()
        idx = int.from_bytes(h[:4], "big") % dim
        val = (int.from_bytes(h[4:8], "big") % 1000) / 1000.0
        vec[idx] += val
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec.tolist()

def call_gemini_embeddings(texts: List[str]) -> List[List[float]]:
    """Generate 384-dimensional vector embeddings cleanly with API key rotation."""
    if not texts:
        return []

    embeddings = []
    batch_size = 50
    for i in range(0, len(texts), batch_size):
        batch_texts = texts[i:i+batch_size]
        payload = []
        for t in batch_texts:
            t_clean = t.strip() if t else "empty"
            payload.append({
                "model": "models/gemini-embedding-001",
                "content": {"parts": [{"text": t_clean}]},
                "outputDimensionality": 384
            })
        
        batch_success = False
        for _ in range(max(1, len(GOOGLE_API_KEYS))):
            active_key = get_current_gemini_key()
            if not active_key:
                break
            try:
                url = f"https://generativelanguage.googleapis.com/v1/models/gemini-embedding-001:batchEmbedContents?key={active_key}"
                res = requests.post(url, json={"requests": payload}, timeout=5.0)
                if res.status_code == 200:
                    res_data = res.json()
                    for emb_obj in res_data.get("embeddings", []):
                        embeddings.append(emb_obj.get("values", []))
                    batch_success = True
                    break
                else:
                    rotate_gemini_key()
            except Exception as e:
                rotate_gemini_key()

        if not batch_success:
            for t in batch_texts:
                embeddings.append(generate_fallback_embedding(t))

    return embeddings

def call_gemini(messages: List[dict], model: str = "gemini-2.0-flash", max_tokens: int = 8192) -> str:
    """Call Google Gemini API for fast, high-quality LLM generation with key rotation."""
    key = get_current_gemini_key()
    if not key:
        raise Exception("GOOGLE_API_KEY not configured")
    
    contents = []
    system_text = ""
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content", "")
        if role == "system":
            system_text = content
        elif role == "user":
            if system_text:
                content = f"{system_text}\n\n{content}"
                system_text = ""
            contents.append({"role": "user", "parts": [{"text": content}]})
        elif role == "assistant":
            contents.append({"role": "model", "parts": [{"text": content}]})
    
    if system_text and contents:
        contents[0]["parts"][0]["text"] = system_text + "\n\n" + contents[0]["parts"][0]["text"]
    elif system_text:
        contents.append({"role": "user", "parts": [{"text": system_text}]})
    
    payload = {
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": 0.2
        }
    }
    
    res = None
    for attempt in range(5):
        active_key = get_current_gemini_key()
        url = f"https://generativelanguage.googleapis.com/v1/models/{model}:generateContent?key={active_key}"
        res = requests.post(url, json=payload, timeout=120)
        if res.status_code == 429:
            rotate_gemini_key()
            time.sleep((attempt + 1) * 1.5)
            continue
        res.raise_for_status()
        break

    if not res:
        raise Exception("Gemini API rate limited after retries")

    res_data = res.json()
    candidates = res_data.get("candidates", [])
    if candidates and "content" in candidates[0]:
        parts = candidates[0]["content"].get("parts", [])
        if parts:
            return parts[0].get("text", "")
    raise Exception(f"Unexpected Gemini response: {res_data}")

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

def call_groq(messages: List[dict], model: str = "llama-3.1-8b-instant", max_tokens: int = 4096, temperature: float = 0.2, timeout: int = 12) -> str:

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
    
    FALLBACK_MODELS = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "gemma2-9b-it"]
    models_to_try = [model] + [m for m in FALLBACK_MODELS if m != model]
    
    for m in models_to_try:
        payload["model"] = m
        try:
            res = requests.post(url, json=payload, headers=headers, timeout=timeout)
            if res.status_code == 429:
                print(f"Groq model '{m}' rate limited (429). Switching to next model...")
                continue
            res.raise_for_status()
            return res.json()["choices"][0]["message"]["content"]
        except Exception as err:
            print(f"Groq model '{m}' notice: {err}")
            continue

    try:
        return call_gemini(messages, max_tokens=max_tokens)
    except Exception as gem_err:
        print(f"Gemini LLM fallback notice: {gem_err}")
        raise HTTPException(status_code=500, detail="LLM service temporarily unavailable. Please retry.")

def call_groq_vision(prompt: str, image_b64: str) -> str:
    if not GROQ_API_KEY:
        raise HTTPException(status_code=500, detail="GROQ_API_KEY not configured.")

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # Use Groq's active vision models (meta-llama/llama-4-scout-17b-16e-instruct)
    VISION_MODELS = [
        "meta-llama/llama-4-scout-17b-16e-instruct",
        "qwen/qwen3.6-27b"
    ]
    
    import time
    max_retries = 3
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
        
        retry_delay = 4.0
        for attempt in range(max_retries):
            try:
                res = requests.post(url, json=payload, headers=headers, timeout=45)
                if res.status_code == 429:
                    print(f"Groq vision model '{vision_model}' rate limited (429). Retrying in {retry_delay}s... (Attempt {attempt+1}/{max_retries})")
                    time.sleep(retry_delay)
                    retry_delay *= 2
                    continue
                res.raise_for_status()
                return res.json()["choices"][0]["message"]["content"]
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"Groq vision model '{vision_model}' attempt {attempt+1} failed: {e}. Retrying in 2s...")
                    time.sleep(2.0)
                    continue
                print(f"Groq vision model '{vision_model}' failed permanently: {e}")
    
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
