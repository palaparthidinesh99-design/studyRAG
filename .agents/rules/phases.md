---
trigger: always_on
---

---
description:
Phase-wise Implementation Plan — StudyRAG
Same principle as every phase before this: each phase has one verifiable deliverable, and nothing in a later phase gets built on an unproven assumption from an earlier one.

Phase 1 — Data layer foundations
Goal: all the storage infrastructure exists and is reachable, before any app logic touches it.

Supabase project created; Postgres tables created: users, subjects, global_books, subject_books, sources, queries
Supabase Storage buckets created: book-pdfs, user-uploads
Chroma Cloud account created, confirm you can create/query a test collection remotely (same sanity check as your very first local Chroma test, just pointed at the cloud endpoint)
Verify: insert one dummy row into each table via Supabase's dashboard; upload one dummy file to each bucket; confirm a test Chroma Cloud collection accepts and returns a query — all manually, no app code yet.

Phase 2 — JWT authentication
Goal: a user can register, log in, and get a valid token; protected routes reject requests without one.

POST /register, POST /login endpoints; bcrypt password hashing; JWT issuance
Middleware/dependency that validates the token on protected routes
Verify: register a real user via curl/Postman, log in, get a token, hit a dummy protected route with and without the token — confirm accept/reject behaves correctly. This is entirely testable in isolation, no other phase depends on it being "smart," just correct.

Phase 3 — Subject management
Goal: an authenticated user can create and list their own subjects.

POST /subjects, GET /subjects — tied to the authenticated user's ID from their JWT
Each subject gets its own Chroma collection created (empty, at creation time)
Verify: as a logged-in test user, create 2 subjects, list them, confirm they're scoped only to that user (a second test user sees none of the first user's subjects).

Phase 4 — Global book ingestion (the shared, reusable content)
Goal: OpenStax PDFs get processed once and stored centrally, reusable across all students.

Reuse your already-working pipeline: pypdf extraction → outline-based chunking → paragraph sub-splitting
POST /admin/global-books (or a manual script, given you're the only one populating this initially) — chunks + embeds into a dedicated global Chroma collection, records it in global_books
Verify: ingest your existing Philosophy book this way, confirm it's queryable, confirm the chunk count/quality matches what you already validated (741 chunks, correct citations).

Phase 5 — Linking global books to a subject
Goal: a student can browse available global books and attach one to their subject, without re-processing it.

GET /global-books (list available), POST /subjects/{id}/books (link existing global book, no re-embedding)
Verify: link the Philosophy book to a test subject; confirm no duplicate embedding work happens (check timing — should be near-instant, just a database row insert).

Phase 6 — Personal source uploads (senior notes, handwritten notes, board photos)
Goal: a student can upload their own content, which gets processed and added to their private subject collection.

POST /subjects/{id}/sources — accepts a file; branches by type:

PDF with real text layer → pypdf path (same as global books, just written into the subject's private collection instead)
Image (handwritten/board photo) → gemma4 OCR path → chunk → embed into subject's private collection


Original file saved to Supabase Storage regardless of type, sources table row created
Verify: upload one clean-text PDF and one photo of handwriting to a test subject; confirm both produce sensible chunks in that subject's private Chroma collection, and that they don't leak into the global book collection or another user's subject.

Phase 7 — Query pipeline, all three input types
Goal: the actual core feature — ask a question, get a grounded answer, searching both the subject's private content and any linked global books together.

POST /subjects/{id}/query/photo — reuses your already-validated OCR → retrieve → generate pipeline
POST /subjects/{id}/query/text — same pipeline, skips the OCR step
POST /subjects/{id}/query/voice — new: test gemma4's audio transcription capability in isolation first (Phase 0-style check) before wiring it into this endpoint
Retrieval step queries both the subject's private collection and any linked global book collections, merges results
Every query gets saved to the queries table — this is your "notes" feature, arriving as a natural side effect of logging
Verify: run all three input types against your test subject (which now has a linked global book + personal uploads), confirm answers are correctly grounded and cite the right source, confirm query history is retrievable via GET /subjects/{id}/history.

Phase 8 — Backend deployment
Goal: the FastAPI backend, fully feature-complete from Phases 1-7, is live on the internet.

Push to GitHub, connect to Render, set environment variables (Supabase keys, Chroma Cloud key, JWT secret)
Verify: hit the deployed /health endpoint, then run the full Phase 7 test again but against the live URL instead of localhost.

Phase 9 — Frontend
Goal: a usable UI — register/login, create subjects, upload sources, submit photo/text/voice queries, view history.

Deploy to Vercel, pointed at the Phase 8 backend URL
Verify: full manual walkthrough as a real user, start to finish, in a browser.

Phase 10 — Polish and edge cases
Goal: handle the messy real-world stuff — failed OCR, empty search results, large file uploads, rate limiting, error states in the UI.

This phase is explicitly open-ended; budget remaining time here rather than a fixed scope.
---
supabase : 
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT UNIQUE NOT NULL,
    hashed_password TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE subjects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) NOT NULL,
    name TEXT NOT NULL,
    chroma_collection_name TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE global_books (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    source TEXT DEFAULT 'openstax',
    chroma_collection_name TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE subject_books (
    subject_id UUID REFERENCES subjects(id) NOT NULL,
    global_book_id UUID REFERENCES global_books(id) NOT NULL,
    PRIMARY KEY (subject_id, global_book_id)
);

CREATE TABLE sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_id UUID REFERENCES subjects(id) NOT NULL,
    source_type TEXT NOT NULL,       -- 'text_pdf' | 'image_ocr'
    title TEXT,
    storage_path TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE queries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_id UUID REFERENCES subjects(id) NOT NULL,
    input_type TEXT NOT NULL,        -- 'photo' | 'text' | 'voice'
    input_storage_path TEXT,
    extracted_text TEXT,
    generated_answer TEXT,
    sections_used JSONB,
    created_at TIMESTAMP DEFAULT now()
);

these are hard coded phases you should be following nothing away unless specified by user