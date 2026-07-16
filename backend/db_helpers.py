import re
import time
import json
import os
import requests
import urllib.parse
from typing import Optional, List, Dict
from fastapi import HTTPException
from backend.config import supabase, chroma_client

# English stop words for NLP keyword extraction
_STOP_WORDS = {
    "a", "an", "the", "is", "it", "its", "in", "on", "at", "to", "of", "for", "and",
    "or", "but", "with", "from", "by", "as", "do", "does", "did", "be", "been", "being",
    "was", "were", "are", "have", "has", "had", "will", "would", "can", "could",
    "should", "may", "might", "must", "shall", "that", "this", "these", "those",
    "what", "which", "who", "whom", "when", "where", "why", "how", "if", "so", "then",
    "also", "just", "very", "more", "most", "much", "many", "some", "any", "no", "not",
    "you", "your", "we", "our", "they", "their", "he", "she", "his", "her", "me", "my",
    "i", "about", "into", "through", "up", "down", "out", "off", "over", "under",
    "again", "further", "then", "once", "here", "there", "all", "both", "each",
    "few", "own", "same", "than", "too", "only", "while", "tell", "me", "please",
    "show", "find", "search", "give", "get", "make", "use", "used", "using",
    "explain", "describe", "define", "summarize", "discuss", "say",
    "question", "answer", "topic", "concept", "information", "related",
}

# Conversational prefixes to strip before keyword extraction
_CONV_PREFIXES = [
    "what is", "what are", "what does", "what do",
    "how to", "how do", "how does", "how can", "how is",
    "can you", "could you", "please", "tell me about", "explain",
    "describe", "give me", "show me", "find", "search for",
    "i want to know", "i want to understand", "in the book",
    "in my notes", "from the notes", "about", "defined as",
    "meaning of", "definition of",
]

def clean_search_query(query: str) -> str:
    q = query.strip()
    q_lower = q.lower()

    for prefix in sorted(_CONV_PREFIXES, key=len, reverse=True):
        if q_lower.startswith(prefix):
            q = q[len(prefix):].strip()
            q_lower = q.lower()
            break

    tokens = re.findall(r'[A-Za-z0-9_+#<>/]+', q)
    keywords = [
        t for t in tokens
        if t.lower() not in _STOP_WORDS and len(t) >= 2
    ]

    if not keywords:
        return query

    return " ".join(keywords)

def rank_and_filter_resources(query_text: str, collections_list: list) -> list:
    keywords = set(clean_search_query(query_text).lower().split())
    if not keywords or len(collections_list) <= 1:
        return collections_list[:2]

    scored = []
    for col in collections_list:
        name_words = set(re.findall(r'\w+', col["source_name"].lower()))
        overlap = keywords.intersection(name_words)
        scored.append((len(overlap), col))

    scored.sort(key=lambda x: x[0], reverse=True)

    if scored[0][0] > 0:
        selected = [item[1] for item in scored if item[0] > 0]
        if len(selected) < 2 and len(collections_list) > 1:
            for item in scored:
                if item[1] not in selected:
                    selected.append(item[1])
                if len(selected) >= 2:
                    break
        return selected[:2]

    return collections_list[:2]

_CHROMA_COLLECTIONS_CACHE = {}

def get_cached_collection(name: str):
    if name not in _CHROMA_COLLECTIONS_CACHE:
        try:
            col = chroma_client.get_collection(name=name)
            _CHROMA_COLLECTIONS_CACHE[name] = col
        except Exception:
            col = chroma_client.get_or_create_collection(name=name)
            _CHROMA_COLLECTIONS_CACHE[name] = col
    return _CHROMA_COLLECTIONS_CACHE[name]

_SUBJECT_CACHE = {}
_CACHE_TTL = 120  # 2 minutes TTL

def get_subject_cached_metadata(subject_id: str, user_id: str) -> dict:
    now = time.time()
    cached = _SUBJECT_CACHE.get(subject_id)
    if cached and (now - cached["timestamp"]) < _CACHE_TTL:
        return cached

    subject = supabase.table("subjects").select("*").eq("id", subject_id).eq("user_id", user_id).execute()
    if not subject.data:
        raise HTTPException(status_code=404, detail="Subject not found or access denied")
    
    collection_name = subject.data[0]["chroma_collection_name"]
    
    ai_note_ids = set()
    try:
        notes_res = supabase.table("sources").select("id").eq("subject_id", subject_id).in_("source_type", ["generated_note", "saved_note"]).execute()
        if notes_res.data:
            ai_note_ids = {n["id"] for n in notes_res.data}
    except Exception as e:
        print(f"Failed to fetch AI notes: {e}")

    linked_books_data = []
    try:
        linked_books = supabase.table("subject_books").select("global_book_id").eq("subject_id", subject_id).execute()
        if linked_books.data:
            book_ids = [lb["global_book_id"] for lb in linked_books.data]
            books = supabase.table("global_books").select("id", "title", "chroma_collection_name").in_("id", book_ids).execute()
            if books.data:
                linked_books_data = books.data
    except Exception as e:
        print(f"Failed to fetch linked books: {e}")

    metadata = {
        "timestamp": now,
        "collection_name": collection_name,
        "ai_note_ids": ai_note_ids,
        "linked_books": linked_books_data
    }
    _SUBJECT_CACHE[subject_id] = metadata
    return metadata

def retrieve_merged_context(subject_id: str, query_text: str, user_id: str, n_results: int = 8, source_filter: str = "all"):
    meta_cache = get_subject_cached_metadata(subject_id, user_id)
    collection_name = meta_cache["collection_name"]
    ai_note_ids = meta_cache["ai_note_ids"]
    linked_books_data = meta_cache["linked_books"]
    
    is_specific_resource = False
    specific_book_info = None
    specific_note_info = None
    
    if source_filter not in ["all", "books", "notes"] and len(source_filter) == 36:
        for b in linked_books_data:
            if b["id"] == source_filter:
                is_specific_resource = True
                specific_book_info = b
                break
        
        if not is_specific_resource:
            note_check = supabase.table("sources").select("*").eq("id", source_filter).execute()
            if note_check.data:
                is_specific_resource = True
                specific_note_info = note_check.data[0]
                
    collections_to_query = []
    
    if is_specific_resource:
        if specific_book_info:
            collections_to_query.append({
                "name": specific_book_info["chroma_collection_name"],
                "type": "global_book",
                "source_name": specific_book_info["title"],
                "book_id": specific_book_info["id"],
                "specific_source_id": None
            })
        elif specific_note_info:
            collections_to_query.append({
                "name": collection_name,
                "type": "personal",
                "source_name": specific_note_info["title"] or "Personal Note",
                "book_id": None,
                "specific_source_id": specific_note_info["id"]
            })
    else:
        if source_filter in ["all", "notes"]:
            collections_to_query.append({
                "name": collection_name,
                "type": "personal",
                "source_name": "Personal Note",
                "book_id": None,
                "specific_source_id": None
            })
        
        if source_filter in ["all", "books"]:
            for b in linked_books_data:
                collections_to_query.append({
                    "name": b["chroma_collection_name"],
                    "type": "global_book",
                    "source_name": b["title"],
                    "book_id": b["id"],
                    "specific_source_id": None
                })
        
        collections_to_query = rank_and_filter_resources(query_text, collections_to_query)
            
    from concurrent.futures import ThreadPoolExecutor

    all_chunks = []
    queries_list = [query_text]
    
    def query_single_collection(col_info):
        chunks = []
        try:
            col = get_cached_collection(col_info["name"])
            query_limit = n_results * 2 if col_info["type"] == "personal" else n_results
            
            where_filter = None
            if col_info.get("specific_source_id"):
                where_filter = {"source_id": col_info["specific_source_id"]}
                
            results = col.query(query_texts=queries_list, n_results=query_limit, where=where_filter)
            
            if results and results.get("documents"):
                seen_chunk_texts = set()
                for query_idx in range(len(results["documents"])):
                    docs = results["documents"][query_idx]
                    metas = results["metadatas"][query_idx] if results.get("metadatas") else [None] * len(docs)
                    dists = results["distances"][query_idx] if results.get("distances") else [0.0] * len(docs)
                    
                    for doc, meta, dist in zip(docs, metas, dists):
                        if not doc:
                            continue
                        doc_clean = doc.strip()
                        if doc_clean in seen_chunk_texts:
                            continue
                        seen_chunk_texts.add(doc_clean)
                        
                        if meta and meta.get("source_id") in ai_note_ids:
                            continue
                            
                        chunks.append({
                            "document": doc,
                            "metadata": meta,
                            "distance": dist,
                            "source_type": col_info["type"],
                            "source_name": col_info["source_name"],
                            "book_id": col_info.get("book_id")
                        })
        except Exception as e:
            print(f"Error querying Chroma collection {col_info['name']}: {e}")
        return chunks

    if collections_to_query:
        with ThreadPoolExecutor(max_workers=max(1, len(collections_to_query))) as executor:
            results_list = list(executor.map(query_single_collection, collections_to_query))
        for chunks in results_list:
            all_chunks.extend(chunks)
            
    all_chunks.sort(key=lambda x: x["distance"])
    return all_chunks[:n_results]

def filter_active_citations(answer: str, sections_used: list) -> list:
    if not answer or not sections_used:
        return []

    answer_lower = answer.lower()
    if "cannot find" in answer_lower and "materials" in answer_lower:
        return []

    ranked = sorted(sections_used, key=lambda s: s.get("distance", 1.0))

    seen = set()
    deduped = []
    for s in ranked:
        key = (s.get("source_name", ""), str(s.get("page", s.get("source_id", ""))))
        if key not in seen:
            seen.add(key)
            deduped.append(s)

    return deduped[:4]

def save_book_url(book_id: str, url: str):
    mapping_path = "cache/book_urls.json"
    mapping = {}
    if os.path.exists(mapping_path):
        try:
            with open(mapping_path, "r") as f:
                mapping = json.load(f)
        except Exception:
            pass
    mapping[book_id] = url
    try:
        with open(mapping_path, "w") as f:
            json.dump(mapping, f)
    except Exception:
        pass

def get_book_url(book_id: str) -> Optional[str]:
    mapping_path = "cache/book_urls.json"
    if os.path.exists(mapping_path):
        try:
            with open(mapping_path, "r") as f:
                mapping = json.load(f)
                return mapping.get(book_id)
        except Exception:
            pass
    return None

def resolve_ia_pdf_url(ident: str) -> Optional[str]:
    try:
        meta = requests.get(f"https://archive.org/metadata/{ident}", timeout=10).json()
        pdf_files = [f["name"] for f in meta.get("files", []) if f.get("name", "").lower().endswith(".pdf")]
        if not pdf_files:
            return None
        
        def pdf_size(name):
            for f in meta.get("files", []):
                if f.get("name") == name:
                    try:
                        return int(f.get("size", 0))
                    except Exception:
                        return 0
            return 0
        pdf_name = max(pdf_files, key=pdf_size)
        return f"https://archive.org/download/{ident}/{urllib.parse.quote(pdf_name)}"
    except Exception as e:
        print(f"Error resolving IA PDF for {ident}: {e}")
        return None
