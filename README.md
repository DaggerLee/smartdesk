# 🤖 SmartDesk — Enterprise Knowledge Assistant

An AI-powered knowledge base and customer support assistant built with RAG (Retrieval-Augmented Generation).
Upload your documents and get instant, accurate answers powered by Google Gemini.

## Features

- 📚 **Knowledge Base Management** — Create, switch, and delete multiple knowledge bases; auto-selects a newly created KB
- 📄 **Document Ingestion** — Upload PDF and TXT files, automatically parsed, chunked, and indexed
- 🗑️ **File Management** — Delete individual files from a knowledge base; removes both SQLite records and ChromaDB vectors
- 🔍 **RAG Pipeline** — Semantic search with ChromaDB vector storage + Gemini streaming generation
- ⚡ **Streaming Responses** — Answers stream word-by-word in real time via Server-Sent Events
- 🎯 **Smart Source Citations** — Sources are shown only when the AI's answer is grounded in uploaded documents (detected via `[SOURCE_USED]` marker); general-knowledge replies show no sources
- 💬 **Conversation History** — All chats saved to SQLite; history restored when switching knowledge bases
- 🖊️ **Auto-Resizing Input** — Textarea grows up to 5 lines as you type, then scrolls; Enter to send, Shift+Enter for new line

## Tech Stack

- **Frontend**: Vue 3, Vite, Axios, Marked
- **Backend**: Python, FastAPI
- **AI**: Google Gemini API (auto-selects best available model)
- **Vector DB**: ChromaDB (local persistent storage, ONNX embeddings)
- **Database**: SQLite via SQLAlchemy

## Getting Started

### Prerequisites

- Python 3.9+
- Node.js 18+
- Google Gemini API Key (free at [aistudio.google.com](https://aistudio.google.com))

### Backend

```bash
cd backend
pip install -r requirements.txt
# Create a .env file with your key:
echo "GEMINI_API_KEY=your_api_key_here" > .env
uvicorn main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173

## Architecture

```
User Query
    ↓
Frontend (Vue 3 + SSE streaming)
    ↓
FastAPI Backend
    ↓
ChromaDB (semantic search) → Top-5 relevant chunks
    ↓
Gemini API (streaming answer with context)
    ↓
[SOURCE_USED] detection → conditional source citations
    ↓
Real-time streamed response to user
```

## Project Structure

```
smartdesk/
├── backend/
│   ├── main.py                  # FastAPI app entry point
│   ├── database.py              # SQLAlchemy setup
│   ├── models.py                # KnowledgeBase, Conversation, UploadedFile
│   ├── chroma_client.py         # ChromaDB operations (add, query, delete)
│   ├── gemini_client.py         # Gemini streaming client + prompt builder
│   └── routers/
│       ├── chat.py              # /api/chat/stream, history endpoints
│       └── knowledge_base.py   # KB CRUD + file upload/delete endpoints
└── frontend/
    └── src/
        ├── App.vue              # Root layout, KB selection state
        ├── api/index.js         # Axios + fetch API client
        └── components/
            ├── ChatWindow.vue       # Chat UI, streaming, file bar
            ├── KnowledgeBaseList.vue # Sidebar, KB create/delete
            └── FileUpload.vue       # File upload button
```

## Background

Built to demonstrate production-grade RAG architecture for enterprise knowledge management.
Inspired by real-world AI assistant integration work done during internships at
Shanghai Intelligent Transportation and Google Maps.
