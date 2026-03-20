# 🤖 SmartDesk — Enterprise Knowledge Assistant

An AI-powered knowledge base and customer support assistant built with RAG (Retrieval-Augmented Generation).
Upload your documents and get instant, accurate answers powered by Google Gemini.

## Screenshots

### Main Interface
![Main](screenshot-main.png)

### AI-Powered Q&A
![Chat](screenshot-chat.png)

## Features
- 📚 **Knowledge Base Management** — Create multiple knowledge bases for different topics
- 📄 **Document Ingestion** — Upload PDF and TXT files, automatically parsed and indexed
- 🔍 **RAG Pipeline** — Semantic search with ChromaDB vector storage + Gemini generation
- 💬 **Conversation History** — All chats saved to SQLite database
- 🎯 **Context-Aware Answers** — AI answers based strictly on your uploaded documents

## Tech Stack
- **Frontend**: Vue 3, Vite
- **Backend**: Python, FastAPI
- **AI**: Google Gemini API (gemini-1.5-flash)
- **Vector DB**: ChromaDB (local persistent storage)
- **Database**: SQLite

## Getting Started

### Prerequisites
- Python 3.9+
- Node.js 18+
- Google Gemini API Key (free at [aistudio.google.com](https://aistudio.google.com))

### Backend
```bash
cd backend
pip install -r requirements.txt
export GEMINI_API_KEY=your_api_key_here
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
Frontend (Vue 3)
    ↓
FastAPI Backend
    ↓
ChromaDB (semantic search) → Top-5 relevant chunks
    ↓
Gemini API (generate answer with context)
    ↓
Response with source-grounded answer
```

## Background
Built to demonstrate production-grade RAG architecture for enterprise knowledge management.
Inspired by real-world AI assistant integration work done during internships at
Shanghai Intelligent Transportation and Google Maps.