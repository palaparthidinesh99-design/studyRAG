# StudyRAG — Academic Study Workspace 🎓🤖

StudyRAG is a premium, publication-grade academic study workspace designed for students to organize, query, and synthesize their study materials. It leverages a modern Hybrid RAG pipeline combining centralized textbook guides with private user notes, PDFs, and board photos.

---

## 🚀 Key Features

*   **Hybrid RAG Retrieval**: Queries cross-reference both global textbook collections (e.g. OpenStax) and private collections (notes, PDF uploads, scans).
*   **Dynamic Tree Notes Generator**: Creates hierarchical, publication-grade study guides with dynamically determined sub-topics, dynamic key-infos, and nested C++ practice problems.
*   **Groq OCR Transcription**: Extracts text from images and board scans with near-perfect alignment.
*   **VS Code Syntax Coloring**: Fully integrates Highlight.js (VS2015 Dark Theme) for beautiful, colorized code blocks in chats and guides.
*   **Polished Dark Aesthetics**: Built with obsidian backgrounds, glassmorphism overlays, and smooth layout animations.

---

## 🛠️ Architecture & Tech Stack

```
                     +---------------------------------------+
                     |         Vercel (HTML/CSS/JS)          |
                     +---------------------------------------+
                                         |
                                         v
                     +---------------------------------------+
                     |         Render (FastAPI Host)         |
                     +---------------------------------------+
                      /                  |                  \
                     v                   v                   v
            +----------------+  +-----------------+  +---------------+
            |  Supabase DB   |  |  Chroma Cloud   |  |   Groq API    |
            | (Postgres/JWT) |  |   (Embeddings)  |  |  (OCR/Vision) |
            +----------------+  +-----------------+  +---------------+
```

### Database Schema (Supabase PostgreSQL)
*   `users`: ID, email, hashed_password, created_at
*   `subjects`: ID, user_id, name, chroma_collection_name, created_at
*   `global_books`: ID, title, source, chroma_collection_name, created_at
*   `subject_books`: subject_id, global_book_id (Primary key)
*   `sources`: ID, subject_id, source_type, title, storage_path, created_at
*   `queries`: ID, subject_id, input_type, input_storage_path, extracted_text, generated_answer, sections_used, created_at

---

## ⚙️ Environment Variables (`.env`)

```ini
SUPABASE_URL=https://your-supabase-project.supabase.co
SUPABASE_KEY=your-supabase-anon-key

CHROMA_API_KEY=your-chroma-cloud-api-key
CHROMA_TENANT=your-chroma-tenant-id
CHROMA_DATABASE=your-chroma-db-name

JWT_SECRET=your-jwt-auth-signing-key
OLLAMA_URL=your-ollama-api-endpoint
OLLAMA_API_KEY=your-ollama-auth-key
GROQ_API_KEY=your-groq-llm-api-key

CLOUDINARY_URL=cloudinary://api_key:api_secret@cloud_name
```

---

## 📦 Local Setup & Execution

### 1. Backend Server Setup
```bash
# Clone the repository and change directory
cd studyRAG

# Create virtual environment and activate it
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run the FastAPI server locally
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 2. Frontend Launch
You can serve the `frontend` folder using any static web server (e.g., Live Server in VS Code, or Python HTTP Server):
```bash
cd frontend
python3 -m http.server 8080
```
Open `http://localhost:8080` in your web browser.

---

## ☁️ Production Deployment

### Backend Deployment (Render)
1. Push your repository to **GitHub**.
2. Connect a new **Web Service** on Render to your GitHub repository.
3. Configure the build and start commands:
   *   **Environment**: `Python`
   *   **Build Command**: `pip install -r requirements.txt`
   *   **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Add all environment variables from your `.env` file to the service's **Environment** tab.

### Frontend Deployment (Vercel)
1. Connect your GitHub repository to **Vercel**.
2. Set the root directory to `frontend`.
3. Set the **Framework Preset** to `Other` (static site).
4. Deploy! Set your backend service URL as the target `BASE_URL` inside `app.js` or through production configuration.
