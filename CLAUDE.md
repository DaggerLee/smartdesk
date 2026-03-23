# SmartDesk — Enterprise Knowledge Assistant

AI-powered RAG chat interface. Users create knowledge bases, upload documents (PDF/TXT),
and ask questions answered by Gemini using ChromaDB vector search. Falls back to Google
web search when document context is insufficient.

---

## Quick Start

**Docker (production):**
```bash
cp .env.example .env        # add your GEMINI_API_KEY
docker-compose up --build
# visit http://localhost
```

**Local dev:**
```bash
# Backend
cd backend && uvicorn main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend && npm run dev
```

Frontend dev server: http://localhost:5173 (Vite proxies /api to backend :8000)

---

## Directory Structure

```
smartdesk/
├── docker-compose.yml           # Orchestrates backend + frontend services           # Orchestrates backend + frontend services
├── .env.example                 # Template: copy to .env and fill GEMINI_API_KEY
├── backend/
│   ├── Dockerfile
│   ├── .dockerignore
│   ├── main.py                  # FastAPI app, CORS, startup DB migration
│   ├── database.py              # SQLAlchemy SQLite engine + SessionLocal
│   ├── models.py                # ORM: KnowledgeBase, Conversation, UploadedFile
│   ├── chroma_client.py         # ChromaDB ops: chunk, add, query, delete
│   ├── gemini_client.py         # Gemini API: streaming, non-streaming, summarize
│   ├── tools.py                 # RAG quality check + Google web search
│   ├── auth.py                  # JWT utils: hash_password, create_access_token, get_current_user
│   ├── requirements.txt
│   ├── .env                     # GEMINI_API_KEY (dev only, not used in Docker)
│   ├── data/                    # Runtime data — gitignored, Docker volume mounted here
│   │   ├── smartdesk.db         # SQLite database
│   │   └── chroma_data/         # ChromaDB persistent vector store
│   └── routers/
│       ├── auth.py              # POST /api/auth/register, POST /api/auth/login
│       ├── chat.py              # POST /api/chat/stream, GET/DELETE /history
│       └── knowledge_base.py    # KB CRUD + file upload/list/delete
├── frontend/
│   ├── Dockerfile               # Multi-stage: node build → nginx serve
│   ├── .dockerignore
│   ├── nginx.conf               # Serves SPA + proxies /api to backend (SSE-safe)
│   └── src/
│       ├── App.vue              # Root layout: sidebar + chat panel
│       ├── api/index.js         # Axios (KB/files) + fetch SSE (chat stream)
│       └── components/
│           ├── AuthPage.vue         # Login / register form (togglable, token saved to localStorage)
│           ├── ChatWindow.vue       # Main chat UI, sources, file chips, summary panel
│           ├── KnowledgeBaseList.vue  # Sidebar: KB list + create modal + user/logout footer
│           └── FileUpload.vue       # Upload button with progress bar
└── CLAUDE.md
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend framework | FastAPI + Uvicorn |
| LLM | Google Gemini API (auto-selects best available model) |
| Vector DB | ChromaDB (local persistent, ONNX embeddings — no PyTorch) |
| Relational DB | SQLite via SQLAlchemy |
| Web search | googlesearch-python (no API key required) |
| PDF parsing | pypdf |
| Frontend | Vue 3 + Vite |
| HTTP client | Axios (REST) + fetch (SSE streaming) |
| Markdown | marked.js |

---

## Database Schema

```sql
knowledge_bases  (id, name, description, created_at)
conversations    (id, kb_id, question, answer, created_at)
uploaded_files   (id, kb_id, filename, chunk_count, uploaded_at, summary)
```

`summary` column was added after initial release. `main.py` runs a safe
`ALTER TABLE uploaded_files ADD COLUMN summary TEXT` on startup to migrate
existing databases without data loss.

---

## Backend Architecture

### Authentication (auth.py + routers/auth.py)

- Passwords hashed with bcrypt via `passlib`
- JWTs signed with HS256 via `python-jose`, expire after 7 days
- `get_current_user` is a FastAPI `Depends` injected into every protected route
- `SECRET_KEY` comes from env var; defaults to a dev placeholder (must change in prod)
- `KnowledgeBase.user_id` links each KB to its owner; all KB/chat queries filter by it
- Existing KBs with `user_id = NULL` (pre-auth data) are not visible to any user

**Frontend auth flow:**
- `AuthPage.vue` handles login + register with a tab toggle
- Token + username stored in `localStorage` (`smartdesk_token`, `smartdesk_username`)
- Axios request interceptor attaches `Authorization: Bearer <token>` to every call
- Axios response interceptor: on 401 → `clearAuth()` + `window.location.reload()`
- `fetch()` (SSE stream) manually reads token from `getToken()`
- `App.vue` checks localStorage on mount; shows `AuthPage` or main app accordingly
- Logout clears localStorage and resets all reactive state (no page reload needed)

### RAG Pipeline (chat.py → POST /api/chat/stream)

1. Validate KB exists, message non-empty
2. Fetch last 5 conversations (multi-turn memory)
3. Query ChromaDB → top-5 chunks **with cosine distances**
4. `assess_rag_quality(results)` — if best distance ≥ 1.0, RAG is insufficient
5. If insufficient → `web_search(question)` via googlesearch-python
6. Build prompt with `_build_prompt(question, context, history, web_results)`
7. Stream Gemini response via SSE
8. Detect `[SOURCE_USED]` / `[WEB_USED]` markers in accumulated response
9. Send typed sources JSON: `{sources: [{type, ...}, ...]}`
10. Strip markers, save clean answer to DB
11. Send `[DONE]`

### Source Types (SSE payload)

```json
// Document source
{"type": "document", "filename": "report.pdf", "preview": "first 80 chars..."}

// Web source
{"type": "web", "title": "Page Title", "url": "https://...", "snippet": "..."}
```

### Prompt Markers

- `[SOURCE_USED]` — Gemini appends this when it used document context
- `[WEB_USED]` — Gemini appends this when it used web search results
- Both are stripped from the answer before saving to DB and display

### Gemini Client (gemini_client.py)

- `_find_model()` — auto-discovers first model supporting `generateContent`, cached
- `_build_prompt(question, context, history, web_results)` — handles 4 cases:
  - docs + web, docs only, web only, no context
- `generate_answer_stream()` — SSE streaming generator
- `generate_answer()` — blocking, used for summaries
- `generate_summary(text)` — summarizes first 4000 chars in 3-5 sentences

### Document Summary Flow (knowledge_base.py)

Upload endpoint uses FastAPI `BackgroundTasks`:
1. Upload response returns immediately after ChromaDB indexing
2. Background task `_generate_and_store_summary(file_id, text)` opens its own
   `SessionLocal()` session (cannot reuse request session), calls `generate_summary()`,
   writes result to `uploaded_files.summary`

### ChromaDB (chroma_client.py)

- Collection per KB: `kb_{kb_id}`
- DefaultEmbeddingFunction (ONNX, local, no external calls)
- Chunk size: 800 chars, overlap: 100 chars
- Prefers paragraph (`\n\n`) > sentence (`。.！？`) boundaries
- `query_documents()` returns `[{text, filename, chunk_index, distance}]`
  — `distance` is cosine distance [0, 2], lower = more relevant

### Tools (tools.py)

```python
RELEVANCE_THRESHOLD = 1.0  # tweak here to make web search trigger more/less often

assess_rag_quality(results) -> bool   # True = docs sufficient, False = search web
web_search(query, num_results=5) -> list[dict]  # fails silently, returns []
```

---

## Frontend Architecture

### ChatWindow.vue

Key state:
- `messages` — `[{id, question, answer, sources, streaming}]`
- `uploadedFiles` — from `GET /api/knowledge-base/{kb_id}/files`
- `activeSummaryFile` — the file whose summary panel is currently open

Source rendering by `src.type`:
- `"document"` → 📄 card (filename + preview), light gray background
- `"web"` → 🌐 card (clickable title link + URL + snippet), light green background

File chip summary:
- Files with a summary show their name underlined+blue (clickable)
- Clicking toggles `activeSummaryFile` → shows amber summary panel below files bar
- Panel shows "generating…" if summary is still null

Streaming:
- `sendMessageStream()` in `api/index.js` uses `fetch()` + `ReadableStream`
- Chunks arrive as `data: "text"`, sources as `data: {"sources":[...]}`
- Blinking cursor `▋` rendered while `msg.streaming === true`

### API Client (api/index.js)

- Axios instance at `/api` for all REST calls
- Raw `fetch()` for SSE streaming (Axios doesn't support streaming)
- `sendMessageStream(kbId, message, onChunk, onSources, onDone)`

---

## Conventions & Rules

- **All file content in English** — comments, UI text, API strings, this file
- **Communicate with user in Chinese**
- No over-engineering: no extra abstractions, no unused fallbacks
- Web search is best-effort: any failure returns `[]`, chat continues normally
- Summary generation is best-effort: failure leaves `summary = null`, UI shows "generating…"

---

## Implemented Features

- [x] Knowledge base CRUD
- [x] File upload: PDF + TXT, chunked and indexed into ChromaDB
- [x] RAG Q&A: semantic search + Gemini answer generation
- [x] SSE streaming responses with blinking cursor
- [x] Source citation: shown only when `[SOURCE_USED]` detected
- [x] Multi-turn conversation memory (last 5 turns injected into prompt)
- [x] Chat history: persisted to SQLite, clearable
- [x] Tool Use — Web search: triggers when RAG relevance insufficient
- [x] Tool Use — Document summary: auto-generated in background after upload
- [x] Typed sources UI: 📄 Document vs 🌐 Web Search with distinct styling
- [x] File summary panel: click file chip name to expand summary
- [x] Docker Compose: one-command deployment, nginx SSE proxy, named volume persistence
- [x] JWT authentication: register/login, bcrypt passwords, 7-day tokens, per-user KB isolation

---

## Docker Setup

| File | Purpose |
|---|---|
| `docker-compose.yml` | Two services: `backend` (FastAPI) + `frontend` (nginx) |
| `backend/Dockerfile` | python:3.11-slim, installs deps, runs uvicorn on 0.0.0.0:8000 |
| `frontend/Dockerfile` | Multi-stage: node:20-alpine builds → nginx:alpine serves |
| `frontend/nginx.conf` | Serves SPA, proxies `/api/` to `http://backend:8000` |

**Critical nginx setting:** `proxy_buffering off` on the `/api/` block — without this,
nginx buffers SSE chunks and streaming appears frozen in the browser.

**Data persistence:** named volume `smartdesk_data` mounted at `/app/data` in the
backend container. Both SQLite (`data/smartdesk.db`) and ChromaDB (`data/chroma_data/`)
write here, so data survives container restarts.

**GEMINI_API_KEY:** passed as environment variable in docker-compose from the root `.env`
file (which is gitignored). Template at `.env.example`.

## Environment

Root `.env` (for Docker):
```
GEMINI_API_KEY=your_key_here
```

`backend/.env` (for local dev, loaded by python-dotenv):
```
GEMINI_API_KEY=your_key_here
```
