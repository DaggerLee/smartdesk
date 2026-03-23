import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

load_dotenv()

# Ensure the data directory exists (used by SQLite and ChromaDB)
Path("data").mkdir(exist_ok=True)

from database import Base, engine
from routers import auth, chat, knowledge_base

# Auto-create all tables (new installs)
Base.metadata.create_all(bind=engine)

# Safe migrations for existing databases — each ALTER is a no-op if the column already exists
_MIGRATIONS = [
    "ALTER TABLE uploaded_files ADD COLUMN summary TEXT",
    "ALTER TABLE knowledge_bases ADD COLUMN user_id INTEGER",
]
with engine.connect() as conn:
    for sql in _MIGRATIONS:
        try:
            conn.execute(text(sql))
            conn.commit()
        except Exception:
            pass  # Column already exists

app = FastAPI(title="SmartDesk API", version="1.0.0")

# Allow the Vite dev server to make cross-origin requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(knowledge_base.router)
app.include_router(chat.router)


@app.get("/")
def root():
    return {"message": "SmartDesk API is running", "version": "1.0.0"}


@app.get("/health")
def health():
    api_key = os.getenv("GEMINI_API_KEY", "")
    return {
        "status": "ok",
        "gemini_configured": bool(api_key and api_key != "your_gemini_api_key_here"),
    }
