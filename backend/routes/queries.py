import os
import uuid
import re
import json
import io
import base64
import requests
import threading
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, BackgroundTasks
from typing import Optional, List
from pydantic import BaseModel

from backend.config import supabase, chroma_client
from backend.auth import get_current_user
from backend.models import QueryTextRequest
from backend.llm import call_groq, call_groq_vision, compress_image
from backend.db_helpers import retrieve_merged_context
from backend.processors import split_into_subchunks

router = APIRouter(prefix="/subjects/{subject_id}", tags=["queries"])

# Core LLM System Prompt used across queries
SYSTEM_PROMPT = """You are a precise, knowledgeable AI study tutor. Your job is to answer the student's question accurately and helpfully with highly visual and structured content.

CORE RULE — ALWAYS ANSWER FROM CONTEXT FIRST:
- If the CONTEXT_FROM_STUDY_MATERIALS block contains ANY text relevant to the question, you MUST base your entire answer on it.
- Find the best matching passage and explain it clearly. Do NOT say "no context found" if context is present.
- Only use general knowledge when the context block explicitly says "No relevant material found". If doing so, explain the answer first and place the note about no matching references found in the study materials at the very end of your response, never at the beginning.

FORMATTING RULES (mandatory):
- Use ## and ### headers to organize your answer into clear sections.
- Use **bold** for every key term, definition, and important phrase.
- Use bullet lists or numbered steps wherever possible.
- PROACTIVELY generate markdown comparison tables (with blank lines before and after) when explaining differences, similarities, or multiple concepts.
- NEVER use Mermaid diagrams or ```mermaid ... ``` code blocks. Instead, represent processes, workflows, classifications, or causal relationships using clean, text-based flow diagrams with Unicode arrows (e.g. `[Step 1] ➔ [Step 2] ➔ [Step 3]`) or a structured step-by-step nested process layout.
- All code blocks MUST be well-structured and declare their programming language (e.g. ```cpp or ```python) on the opening fence. This enables the theme-based syntax formatting (similar to VS Code).
- Keep your answer focused and concise — avoid padding or generic introductions.
- At the very end, on its own line, write: CITED_SOURCE: [Exact Label]
  Examples: CITED_SOURCE: [Philosophy, Introduction, p.212] or CITED_SOURCE: [Upload: MyNotes]
"""

def parse_cited_source(answer: str, sections_used: list) -> tuple[str, list]:
    cited_label = None
    new_lines = []
    for line in answer.split("\n"):
        if line.strip().startswith("CITED_SOURCE:"):
            match = re.search(r'CITED_SOURCE:\s*(.+)', line, re.IGNORECASE)
            if match:
                cited_label = match.group(1).strip()
            continue
        new_lines.append(line)
        
    cleaned_answer = "\n".join(new_lines).strip()
    
    active_sources = []
    if cited_label and sections_used:
        norm_label = cited_label.replace("[", "").replace("]", "").strip().lower()
        for sec in sections_used:
            if sec["source_type"] == "global_book":
                ref_str = f"{sec['source_name']}, {sec['section']}, p.{sec['page']}"
            else:
                ref_str = f"Upload: {sec['source_name']}"
                
            if norm_label in ref_str.lower() or ref_str.lower() in norm_label:
                active_sources.append(sec)
                break
                
    if not active_sources and sections_used:
        active_sources.append(sections_used[0])
        
    return cleaned_answer, active_sources

def get_subject_materials_info(subject_id: str, subject_name: str) -> str:
    try:
        # Fetch personal sources (uploaded documents/notes)
        personal_res = supabase.table("sources").select("title").eq("subject_id", subject_id).execute()
        personal_titles = [s["title"] for s in personal_res.data] if personal_res.data else []
        
        # Fetch linked global books
        linked_books = supabase.table("subject_books").select("global_book_id").eq("subject_id", subject_id).execute()
        book_titles = []
        if linked_books.data:
            book_ids = [lb["global_book_id"] for lb in linked_books.data]
            books = supabase.table("global_books").select("title").in_("id", book_ids).execute()
            book_titles = [b["title"] for b in books.data] if books.data else []
            
        all_material_titles = personal_titles + book_titles
        materials_str = ", ".join([f"'{t}'" for t in all_material_titles]) if all_material_titles else "None uploaded/linked yet"
    except Exception as e:
        print(f"Failed to fetch study guide materials info: {e}")
        materials_str = "None"
        
    return f"ACTIVE SUBJECT: {subject_name}\nAVAILABLE STUDY MATERIALS: {materials_str}"


@router.post("/query/text")
def query_text(
    subject_id: str,
    req: QueryTextRequest,
    user_id: str = Depends(get_current_user)
):
    subject = supabase.table("subjects").select("*").eq("id", subject_id).eq("user_id", user_id).execute()
    if not subject.data:
        raise HTTPException(status_code=404, detail="Subject not found or access denied")

    # Fast intercept for greetings
    greeting_pattern = re.compile(
        r"^(hi|hello|hey|greetings|howdy|what'?s up|how are you|thanks|thank you|good morning|good afternoon|good evening)\b", 
        re.IGNORECASE
    )
    if greeting_pattern.match(req.query.strip()) and len(req.query.strip()) < 40:
        try:
            prompt = f"You are a friendly AI study assistant. The user said: '{req.query}'. Respond warmly, simply, and concisely."
            messages = [{"role": "user", "content": prompt}]
            answer = call_groq(messages)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"LLM generation failed: {str(e)}")
            
        try:
            supabase.table("queries").insert({
                "subject_id": subject_id,
                "input_type": "text",
                "extracted_text": json.dumps([req.query]),
                "generated_answer": json.dumps([answer]),
                "sections_used": [[]]
            }).execute()
        except Exception:
            pass
            
        return {
            "query": req.query,
            "answer": answer,
            "sources": []
        }

    subject_name = subject.data[0]["name"]
    materials_info = get_subject_materials_info(subject_id, subject_name)

    questions_list = []
    answers_list = []
    citations_history = []
    
    if req.query_id:
        try:
            existing_res = supabase.table("queries").select("*").eq("id", req.query_id).execute()
            if existing_res.data:
                row = existing_res.data[0]
                try:
                    questions_list = json.loads(row["extracted_text"])
                    if not isinstance(questions_list, list):
                        questions_list = [row["extracted_text"]]
                except Exception:
                    questions_list = [row["extracted_text"]]
                
                try:
                    answers_list = json.loads(row["generated_answer"])
                    if not isinstance(answers_list, list):
                        answers_list = [row["generated_answer"]]
                except Exception:
                    answers_list = [row["generated_answer"]]
                
                try:
                    citations_history = json.loads(row["sections_used"])
                    if not isinstance(citations_history, list) or (len(citations_history) > 0 and not isinstance(citations_history[0], list)):
                        citations_history = [citations_history]
                except Exception:
                    citations_history = [row["sections_used"]]
        except Exception as e:
            print(f"Failed to fetch session history: {e}")

    retrieval_text = req.query
    if questions_list:
        retrieval_text = f"{req.query} {questions_list[-1]}"
        
    retrieved = retrieve_merged_context(subject_id, retrieval_text, user_id, n_results=8, source_filter=req.source_filter or "all")
    RELEVANCE_THRESHOLD = 1.80
    retrieved = [c for c in retrieved if c.get("distance", 0.0) <= RELEVANCE_THRESHOLD]
    
    context_parts = []
    sections_used = []
    
    for chunk in retrieved:
        doc = chunk["document"]
        meta = chunk["metadata"]
        source_name = chunk["source_name"]
        
        if chunk["source_type"] == "global_book":
            section = meta.get("section_title", "Unknown Section")
            page = meta.get("start_page", "Unknown")
            ref_str = f"[{source_name}, {section}, p.{page}]"
            sections_used.append({
                "source_type": "global_book",
                "source_name": source_name,
                "source_id": chunk.get("book_id", ""),
                "section": section,
                "page": page,
                "distance": chunk.get("distance", 1.0),
                "text": doc
            })
        else:
            source_title = meta.get('source_title', 'Personal Note') if meta else 'Personal Note'
            source_id = meta.get('source_id', '') if meta else ''
            ref_str = f"[Upload: {source_title}]"
            sections_used.append({
                "source_type": meta.get('source_type', 'personal') if meta else 'personal',
                "source_name": source_title,
                "source_id": source_id,
                "distance": chunk.get("distance", 1.0),
                "text": doc
            })
        context_parts.append(f"{ref_str}\n{doc}")
        
    context = "\n\n---\n\n".join(context_parts)
    # Truncate context to avoid Groq 413 Payload Too Large errors (cap at ~6000 chars)
    if len(context) > 6000:
        context = context[:6000] + "\n...[context truncated for length]..."
    if context:
        context_block = f"<CONTEXT_FROM_STUDY_MATERIALS>\n{context}\n</CONTEXT_FROM_STUDY_MATERIALS>"
    else:
        context_block = "<CONTEXT_FROM_STUDY_MATERIALS>\n(No relevant material found in the linked resources — answer from general academic knowledge.)\n</CONTEXT_FROM_STUDY_MATERIALS>"

    explain_depth_instruction = ""
    if "explain in depth" in req.query.lower() or "explain in-depth" in req.query.lower():
        explain_depth_instruction = "- The student explicitly asked for in-depth explanation. Be thorough, include mechanism, worked examples, and edge cases.\n"

    prompt = f"""Use the retrieved study material below to answer the student's question.

STUDY ENVIRONMENT DETAILS:
{materials_info}

{context_block}

INSTRUCTIONS:
- You MUST read all passages in the context block above and find the one most relevant to the question.
- If a relevant passage exists, base your answer directly on it — paraphrase, explain, and expand from that content.
- Do NOT include raw citation labels like [Book, Section, p.X] inline in your answer body. Only in CITED_SOURCE at the end.
- If the context block says "No relevant material found" or contains no matching context, answer the question using your general academic knowledge, ensuring your explanation is customized and styled to fit the active subject '{subject_name}'. At the very end of your response (and ONLY at the end), add a single short line: 'Note: No direct matching references found in the uploaded study materials.'
{explain_depth_instruction}
Student's Question: {req.query}
"""

    history_messages = []
    if questions_list and answers_list:
        last_q = questions_list[-1]
        last_a = answers_list[-1]
        clean_lines = [l for l in last_a.split("\n") if not l.strip().startswith("CITED_SOURCE:")]
        clean_last_a = "\n".join(clean_lines).strip()
        history_messages.append({"role": "user", "content": last_q})
        history_messages.append({"role": "assistant", "content": clean_last_a})

    try:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(history_messages)
        messages.append({"role": "user", "content": prompt})
        answer = call_groq(messages)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM generation failed: {str(e)}")
        
    cleaned_answer, active_sources = parse_cited_source(answer, sections_used)
    
    questions_list.append(req.query)
    answers_list.append(cleaned_answer)
    citations_history.append(active_sources)
    
    final_query_id = req.query_id
    try:
        if final_query_id:
            supabase.table("queries").update({
                "extracted_text": json.dumps(questions_list),
                "generated_answer": json.dumps(answers_list),
                "sections_used": citations_history
            }).eq("id", final_query_id).execute()
        else:
            db_res = supabase.table("queries").insert({
                "subject_id": subject_id,
                "input_type": "text",
                "extracted_text": json.dumps(questions_list),
                "generated_answer": json.dumps(answers_list),
                "sections_used": citations_history
            }).execute()
            if db_res.data:
                final_query_id = db_res.data[0]["id"]
    except Exception as e:
        print(f"Failed to log query session to database: {e}")
        
    return {
        "id": final_query_id,
        "query": req.query,
        "answer": cleaned_answer,
        "sources": active_sources
    }


@router.post("/query/photo")
async def query_photo(
    subject_id: str,
    file: UploadFile = File(...),
    source_filter: str = "all",
    query_id: Optional[str] = Form(None),
    user_id: str = Depends(get_current_user)
):
    subject = supabase.table("subjects").select("*").eq("id", subject_id).eq("user_id", user_id).execute()
    if not subject.data:
        raise HTTPException(status_code=404, detail="Subject not found or access denied")
    
    subject_name = subject.data[0]["name"]
    materials_info = get_subject_materials_info(subject_id, subject_name)
    
    file_content = await file.read()
    file_ext = os.path.splitext(file.filename)[1].lower()
    if file_ext not in [".png", ".jpg", ".jpeg", ".webp"]:
        raise HTTPException(status_code=400, detail="Invalid image format. Must be PNG, JPG, JPEG or WEBP.")
    
    try:
        compressed_content = compress_image(file_content)
        from backend.config import upload_to_cloudinary
        storage_path = upload_to_cloudinary(
            compressed_content, 
            file.filename, 
            folder=f"{user_id}/{subject_id}/queries"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to compress or upload image to Cloudinary: {str(e)}")
        
    try:
        compressed_content = compress_image(file_content)
        image_b64 = base64.b64encode(compressed_content).decode("utf-8")
        extracted_text = call_groq_vision(
            "Transcribe all text in this image exactly as written. If it is a question, return just the question.",
            image_b64
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"OCR transcription failed: {str(e)}")
        
    questions_list = []
    answers_list = []
    citations_history = []
    
    if query_id:
        try:
            existing_res = supabase.table("queries").select("*").eq("id", query_id).execute()
            if existing_res.data:
                row = existing_res.data[0]
                try:
                    questions_list = json.loads(row["extracted_text"])
                    if not isinstance(questions_list, list):
                        questions_list = [row["extracted_text"]]
                except Exception:
                    questions_list = [row["extracted_text"]]
                
                try:
                    answers_list = json.loads(row["generated_answer"])
                    if not isinstance(answers_list, list):
                        answers_list = [row["generated_answer"]]
                except Exception:
                    answers_list = [row["generated_answer"]]
                
                try:
                    citations_history = json.loads(row["sections_used"])
                    if not isinstance(citations_history, list) or (len(citations_history) > 0 and not isinstance(citations_history[0], list)):
                        citations_history = [citations_history]
                except Exception:
                    citations_history = [row["sections_used"]]
        except Exception as e:
            print(f"Failed to fetch session history: {e}")

    retrieval_text = extracted_text
    if questions_list:
        retrieval_text = f"{extracted_text} {questions_list[-1]}"
        
    retrieved = retrieve_merged_context(subject_id, retrieval_text, user_id, n_results=8, source_filter=source_filter)
    RELEVANCE_THRESHOLD = 1.80
    retrieved = [c for c in retrieved if c.get("distance", 0.0) <= RELEVANCE_THRESHOLD]
    
    context_parts = []
    sections_used = []
    
    for chunk in retrieved:
        doc = chunk["document"]
        meta = chunk["metadata"]
        source_name = chunk["source_name"]
        
        if chunk["source_type"] == "global_book":
            section = meta.get("section_title", "Unknown Section")
            page = meta.get("start_page", "Unknown")
            ref_str = f"[{source_name}, {section}, p.{page}]"
            sections_used.append({
                "source_type": "global_book",
                "source_name": source_name,
                "source_id": chunk.get("book_id", ""),
                "section": section,
                "page": page,
                "distance": chunk.get("distance", 1.0),
                "text": doc
            })
        else:
            source_title = meta.get('source_title', 'Personal Note') if meta else 'Personal Note'
            source_id = meta.get('source_id', '') if meta else ''
            ref_str = f"[Upload: {source_title}]"
            sections_used.append({
                "source_type": meta.get('source_type', 'personal') if meta else 'personal',
                "source_name": source_title,
                "source_id": source_id,
                "distance": chunk.get("distance", 1.0),
                "text": doc
            })
        context_parts.append(f"{ref_str}\n{doc}")
        
    context = "\n\n---\n\n".join(context_parts)
    if context:
        context_block = f"<CONTEXT_FROM_STUDY_MATERIALS>\n{context}\n</CONTEXT_FROM_STUDY_MATERIALS>"
    else:
        context_block = "<CONTEXT_FROM_STUDY_MATERIALS>\n(No relevant material found in the linked resources — answer from general academic knowledge.)\n</CONTEXT_FROM_STUDY_MATERIALS>"

    explain_depth_instruction = ""
    if "explain in depth" in extracted_text.lower() or "explain in-depth" in extracted_text.lower():
        explain_depth_instruction = "- The student explicitly asked for in-depth explanation. Be thorough, include mechanism, worked examples, and edge cases.\n"

    prompt = f"""Use the retrieved study material below to answer the student's question.

STUDY ENVIRONMENT DETAILS:
{materials_info}

{context_block}

INSTRUCTIONS:
- You MUST read all passages in the context block above and find the one most relevant to the question.
- If a relevant passage exists, base your answer directly on it — paraphrase, explain, and expand from that content.
- Do NOT include raw citation labels like [Book, Section, p.X] inline in your answer body. Only in CITED_SOURCE at the end.
- If the context block says "No relevant material found" or contains no matching context, answer the question using your general academic knowledge, ensuring your explanation is customized and styled to fit the active subject '{subject_name}'. At the very end of your response (and ONLY at the end), add a single short line: 'Note: No direct matching references found in the uploaded study materials.'
{explain_depth_instruction}
Student's Question: {extracted_text}
"""

    history_messages = []
    if questions_list and answers_list:
        last_q = questions_list[-1]
        last_a = answers_list[-1]
        clean_lines = [l for l in last_a.split("\n") if not l.strip().startswith("CITED_SOURCE:")]
        clean_last_a = "\n".join(clean_lines).strip()
        history_messages.append({"role": "user", "content": last_q})
        history_messages.append({"role": "assistant", "content": clean_last_a})

    try:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(history_messages)
        messages.append({"role": "user", "content": prompt})
        answer = call_groq(messages, max_tokens=2048)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM generation failed: {str(e)}")
        
    cleaned_answer, active_sources = parse_cited_source(answer, sections_used)
    
    questions_list.append(extracted_text)
    answers_list.append(cleaned_answer)
    citations_history.append(active_sources)
        
    final_query_id = query_id
    try:
        if final_query_id:
            supabase.table("queries").update({
                "extracted_text": json.dumps(questions_list),
                "generated_answer": json.dumps(answers_list),
                "sections_used": citations_history
            }).eq("id", final_query_id).execute()
        else:
            db_res = supabase.table("queries").insert({
                "subject_id": subject_id,
                "input_type": "photo",
                "input_storage_path": storage_path,
                "extracted_text": json.dumps(questions_list),
                "generated_answer": json.dumps(answers_list),
                "sections_used": citations_history
            }).execute()
            if db_res.data:
                final_query_id = db_res.data[0]["id"]
    except Exception as e:
        print(f"Failed to log query session to database: {e}")
        
    return {
        "id": final_query_id,
        "extracted_text": extracted_text,
        "answer": cleaned_answer,
        "sources": active_sources
    }


class TriggerNotesRequest(BaseModel):
    source_id: str
    topics: List[str]

def generate_notes_background_task(
    subject_id: str,
    user_id: str,
    source_id: Optional[str],
    topics: List[str],
    generated_note_source_id: str,
    collection_name: str
):
    try:
        from pypdf import PdfReader
        from concurrent.futures import ThreadPoolExecutor
        from backend.config import download_file_bytes
        
        raw_text = ""
        # Only fetch and download if source_id is provided
        if source_id:
            # 1. Fetch the source details
            src_res = supabase.table("sources").select("*").eq("id", source_id).execute()
            if not src_res.data:
                print("Background notes gen failed: source document not found.")
                return
            
            src_data = src_res.data[0]
            storage_path = src_data["storage_path"]
            title = src_data["title"]
            
            # 2. Download raw text/content of the source document
            try:
                file_bytes = download_file_bytes(storage_path)
                source_type = src_data.get("source_type", "")
                
                if source_type == "text_pdf" or title.lower().endswith(".pdf"):
                    file_ext = ".pdf"
                elif source_type == "image_ocr" or any(ext in title.lower() for ext in [".png", ".jpg", ".jpeg", ".webp"]):
                    file_ext = ".jpg"
                else:
                    file_ext = os.path.splitext(title.lower())[1]
                
                if file_ext == ".pdf":
                    pdf_file = io.BytesIO(file_bytes)
                    reader = PdfReader(pdf_file)
                    for page in reader.pages:
                        text = page.extract_text()
                        if text:
                            raw_text += text + "\n"
                elif file_ext in [".jpg", ".jpeg", ".png", ".webp"]:
                    from backend.llm import call_groq_vision, compress_image
                    import base64
                    compressed = compress_image(file_bytes)
                    img_b64 = base64.b64encode(compressed).decode("utf-8")
                    raw_text = call_groq_vision(
                        "Transcribe all text in this image exactly as written. Preserve paragraphs.",
                        img_b64
                    )
                else:
                    raw_text = file_bytes.decode("utf-8")
            except Exception as e:
                print(f"Background notes gen: failed to download/parse original source: {e}")
                try:
                    supabase.table("sources").update({
                        "storage_path": f"failed:Failed to download original source document: {str(e)}"
                    }).eq("id", generated_note_source_id).execute()
                except Exception:
                    pass
                return
                
        note_title = "Study Guide"
        try:
            note_src = supabase.table("sources").select("title").eq("id", generated_note_source_id).execute()
            if note_src.data:
                raw_title = note_src.data[0]["title"]
                note_title = raw_title.replace("AI Notes - ", "").replace("AI Notes -", "").strip()
        except Exception:
            pass

        # Build a single unified prompt to generate the entire study guide in one pass
        topics_str = ", ".join([f'"{t}"' for t in topics])
        
        # Retrieve context/RAG for all topics together to enhance accuracy
        rag_context = ""
        try:
            context_segments = []
            for t in topics[:3]: # Search first few major topics to keep context compact
                chunks = retrieve_merged_context(subject_id, t, user_id, n_results=1, source_filter="all")
                if chunks:
                    context_segments.append(chunks[0]['document'])
            if context_segments:
                rag_context = "\n\n--- ADDITIONAL CONTEXT ---\n" + "\n\n".join(context_segments)
        except Exception as rag_err:
            print(f"RAG search failed for unified notes: {rag_err}")

        status_msg = f"processing:30:Generating unified study guide with Ollama...:{generated_note_source_id}"
        try:
            supabase.table("sources").update({"storage_path": status_msg}).eq("id", generated_note_source_id).execute()
        except Exception:
            pass

        prompt = f"""You are a university professor writing a publication-grade, highly structured academic study guide for a student preparing for exams.

Your task: Write a cohesive, tree-structured study guide explaining the following selected topics: {topics_str}.

You MUST organize the guide as a tree hierarchy based on the student's resource material:
1. **PARENT TOPICS (`##`)**: For each major selected topic, create a main heading using `## [Parent Topic Name]` (e.g. `## Constructors`).
2. **DYNAMIC SUB-TOPICS (`###`)**: Under each parent topic, dynamically define 2 to 4 key sub-topics using `### [Sub-Topic Name]` (e.g. `### Copy Constructor`). 
   - Note: If any other selected topics logically belong as children of a parent topic, place them here as sub-topics.
   - You are also encouraged to add new sub-topics that the student might have missed but are necessary to explain the parent topic thoroughly.
3. **DYNAMIC KEY INFOS (4 to 5 elements per sub-topic)**: For each sub-topic, write a detailed deep-dive explanation. You must dynamically choose and explain **4 to 5 key important elements** (such as syntax, core mechanics, safety rules, design tradeoffs, or common developer mistakes) to make the explanation complete and structured.
4. **SUB-TOPIC PRACTICE PROBLEMS**: Conclude each sub-topic with a targeted C++ code snippet or exam-style practice question followed immediately by its step-by-step worked solution.
5. **HEADING LEVEL RULES**: Only use `##` for parent topics and `###` for sub-topics. NEVER use `####` or lower headings to ensure clean visual layers in the student's viewer.

STRICT Formatting and Semantics Rules:
- **NO DUPLICATION**: Do not repeat definitions or sub-topics across different sections. Explain each concept once, in its most appropriate subtree location.
- **DEPTH**: Write detailed, comprehensive explanations for all sub-topics. Do not crop or artificially truncate the explanations; ensure they have complete academic depth.
- **TABLES**: PROACTIVELY generate markdown comparison tables (with blank lines before and after) to contrast different sub-topics, parameters, or types.
- **NO MERMAID**: NEVER use Mermaid code blocks or Mermaid syntax. Instead, visually represent workflows, lifecycles, or processes using a clean, text-based flow diagram using Unicode arrows (e.g. `[Step 1] ➔ [Step 2] ➔ [Step 3]`) or a structured step-by-step nested process layout.
- **CODE BLOCKS**: All code blocks MUST declare C++ syntax (` ```cpp `) on the opening fence and be well-structured.

STUDENT'S RESOURCE MATERIAL:
{raw_text[:8000]}
{rag_context}"""

        from backend.llm import call_groq, call_ollama_fallback
        full_guide = ""
        try:
            messages = [{"role": "user", "content": prompt}]
            # Try Groq (using Groq's llama-3.3-70b-versatile model)
            full_guide = call_groq(messages, model="llama-3.3-70b-versatile", max_tokens=4000)
        except Exception as e:
            print(f"Groq notes generation failed: {e}. Falling back to Ollama...")
            try:
                full_guide = call_ollama_fallback(messages, max_tokens=4000)
            except Exception as ollama_err:
                print(f"Ollama notes generation fallback failed: {ollama_err}")
                full_guide = f"# {note_title}\n\n*Error: Failed to generate study notes using Groq and Ollama: {str(ollama_err)}*"

        if not full_guide.startswith("# "):
            full_guide = f"# {note_title}\n\n" + full_guide
        
        # No summary step — the per-topic sections are already comprehensive
        
        # 5. Upload final Markdown file to Supabase Storage
        note_content_bytes = full_guide.encode("utf-8")
        dest_storage_path = f"{user_id}/{subject_id}/generated-notes/{uuid.uuid4().hex}.md"
        
        supabase.storage.from_("user-uploads").upload(
            path=dest_storage_path,
            file=note_content_bytes,
            file_options={"content-type": "text/markdown"}
        )
        
        # 6. Update the source row to point to the actual storage path
        supabase.table("sources").update({
            "storage_path": dest_storage_path
        }).eq("id", generated_note_source_id).execute()
        
        # 7. Chunk and Index in Chroma DB so it is searchable by chatbot
        chunks = split_into_subchunks(full_guide)
        if chunks:
            try:
                collection = chroma_client.get_or_create_collection(name=collection_name)
                ids = [f"source_chunk_{uuid.uuid4().hex}" for _ in range(len(chunks))]
                metadatas = [
                    {
                        "source_id": generated_note_source_id,
                        "source_title": f"AI Notes - {os.path.splitext(title)[0]}",
                        "chunk_index": i
                    }
                    for i in range(len(chunks))
                ]
                batch_size = 100
                for i in range(0, len(chunks), batch_size):
                    collection.add(
                        ids=ids[i:i+batch_size],
                        documents=chunks[i:i+batch_size],
                        metadatas=metadatas[i:i+batch_size]
                    )
            except Exception as e:
                print(f"Chroma DB indexing error for generated guide: {e}")
                
        print(f"Background notes generation completed successfully for {generated_note_source_id}.")
        
    except Exception as general_err:
        print(f"Background notes generation thread crashed: {general_err}")
        try:
            supabase.table("sources").update({
                "storage_path": f"failed:Generation crashed: {str(general_err)}"
            }).eq("id", generated_note_source_id).execute()
        except Exception:
            pass

@router.post("/generate-notes/analyze")
async def analyze_notes_outline(
    subject_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user)
):
    subject = supabase.table("subjects").select("*").eq("id", subject_id).eq("user_id", user_id).execute()
    if not subject.data:
        raise HTTPException(status_code=404, detail="Subject not found or access denied")
        
    file_content = await file.read()
    file_ext = os.path.splitext(file.filename)[1].lower()
    
    raw_text = ""
    source_type = "text_pdf"
    if file_ext == ".pdf":
        from pypdf import PdfReader
        try:
            pdf_file = io.BytesIO(file_content)
            reader = PdfReader(pdf_file)
            for idx, page in enumerate(reader.pages):
                text = page.extract_text()
                if text:
                    raw_text += f"\n--- [Page {idx + 1}] ---\n{text}\n"
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to parse PDF text: {str(e)}")
    elif file_ext in [".png", ".jpg", ".jpeg", ".webp"]:
        source_type = "image_ocr"
        try:
            compressed_content = compress_image(file_content)
            image_b64 = base64.b64encode(compressed_content).decode("utf-8")
            raw_text = call_groq_vision(
                "Transcribe all text in this image exactly as written. Preserve paragraphs.",
                image_b64
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"OCR transcription failed: {str(e)}")
    else:
        raise HTTPException(status_code=400, detail="Unsupported file format. Must be PDF or Image.")
        
    if not raw_text.strip():
        raise HTTPException(status_code=400, detail="No readable text found in the uploaded file.")
        
    # Save the original file — source_type is 'notes_input' so it does NOT appear in Resources
    storage_path = f"notes_input_placeholder/{subject_id}/{uuid.uuid4().hex}"
    try:
        from backend.config import upload_to_cloudinary
        storage_path = upload_to_cloudinary(file_content, file.filename, folder=f"{user_id}/{subject_id}/notes-inputs")
    except Exception as e:
        print(f"Warning: Cloudinary upload failed for notes input: {e}. Using placeholder path.")
        # Non-fatal: we still have the text in raw_text
        
    try:
        source_insert = supabase.table("sources").insert({
            "subject_id": subject_id,
            "source_type": "notes_input",
            "title": file.filename,
            "storage_path": storage_path
        }).execute()
        source_data = source_insert.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create database record: {str(e)}")
        
    # Index original file in background only for PDF (text extraction already done)
    if source_type == "text_pdf":
        from backend.tasks import index_source_task
        background_tasks.add_task(
            index_source_task,
            source_data["id"],
            subject_id,
            file_content,
            file.filename,
            subject.data[0]["chroma_collection_name"],
            source_type
        )
    
    # Call fast LLM to extract key conceptual topics only
    outline_prompt = f"""Analyze the educational text below and identify a list of 3 to 6 major conceptual academic topics (e.g. "Constructors", "Destructors", "Operator Overloading", "Virtual Functions", "Abstract Classes", "Friend Functions").

CRITICAL RULES:
1. CONCEPTUAL TOPICS ONLY: Extract only major conceptual headers.
2. DO NOT EXTRACT SUBSECTIONS: Do NOT include subheadings, worked examples, practice problems, diagrams, definitions, summaries, introduction, characteristics, or conclusions as separate topics. They must be merged into the parent conceptual topic.
3. CHRONOLOGICAL ORDER: The list of topics MUST be in the exact order they appear in the text.
4. Return ONLY a valid JSON list of strings, e.g. ["Topic A", "Topic B"]. Do not return markdown, preamble, or formatting blocks.

TEXT:
{raw_text[:12000]}

JSON:"""
    
    topics = []
    try:
        from backend.llm import call_groq
        messages = [{"role": "user", "content": outline_prompt}]
        res_text = call_groq(messages, model="llama-3.1-8b-instant", max_tokens=1000)
        
        # Robustly find and parse the JSON array in the response
        import ast
        start_idx = res_text.find("[")
        end_idx = res_text.rfind("]")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            json_str = res_text[start_idx:end_idx+1]
            try:
                topics = json.loads(json_str)
            except Exception:
                try:
                    topics = ast.literal_eval(json_str)
                except Exception:
                    pass
        else:
            json_text = res_text.strip()
            if json_text.startswith("```json"):
                json_text = json_text.split("```json")[1].split("```")[0].strip()
            elif json_text.startswith("```"):
                json_text = json_text.split("```")[1].split("```")[0].strip()
            try:
                topics = json.loads(json_text)
            except Exception:
                try:
                    topics = ast.literal_eval(json_text)
                except Exception:
                    pass
    except Exception as e:
        print(f"Failed to extract outline topics: {e}")
        # Default fallback topics
        topics = ["Key Term Definitions", "Core Principles & Mechanics", "Practical Applications", "Practice Questions"]
        
    return {
        "source_id": source_data["id"],
        "title": file.filename,
        "topics": topics
    }

@router.post("/generate-notes/trigger")
def trigger_notes_generation(
    subject_id: str,
    req: TriggerNotesRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user)
):
    subject = supabase.table("subjects").select("*").eq("id", subject_id).eq("user_id", user_id).execute()
    if not subject.data:
        raise HTTPException(status_code=404, detail="Subject not found or access denied")
        
    collection_name = subject.data[0]["chroma_collection_name"]
    
    # 1. Fetch original file details
    src_res = supabase.table("sources").select("title").eq("id", req.source_id).execute()
    if not src_res.data:
        raise HTTPException(status_code=404, detail="Original source document not found.")
        
    title = f"AI Notes - {os.path.splitext(src_res.data[0]['title'])[0]}"
    
    # 2. Create the placeholder generated source with a processing status in storage_path
    task_uuid = uuid.uuid4().hex
    status_path = f"processing:0:Initializing study guide generation...:{task_uuid}"
    
    try:
        source_insert = supabase.table("sources").insert({
            "subject_id": subject_id,
            "source_type": "generated_note",
            "title": title,
            "storage_path": status_path
        }).execute()
        generated_note_data = source_insert.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create generated note placeholder: {str(e)}")
        
    # 3. Spin off background notes generation using python threading
    # We use a daemon thread so it runs independent of request termination (Vercel exception)
    thread = threading.Thread(
        target=generate_notes_background_task,
        args=(subject_id, user_id, req.source_id, req.topics, generated_note_data["id"], collection_name)
    )
    thread.daemon = True
    thread.start()
    
    return {
        "id": generated_note_data["id"],
        "title": title,
        "message": "Generation started in the background.",
        "source": generated_note_data
    }

@router.delete("/history/{query_id}")
def delete_query(
    subject_id: str,
    query_id: str,
    user_id: str = Depends(get_current_user)
):
    subject = supabase.table("subjects").select("*").eq("id", subject_id).eq("user_id", user_id).execute()
    if not subject.data:
        raise HTTPException(status_code=404, detail="Subject not found or access denied")
        
    supabase.table("queries").delete().eq("id", query_id).eq("subject_id", subject_id).execute()
    return {"status": "success"}
