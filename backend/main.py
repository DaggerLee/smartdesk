import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect, text

load_dotenv()

# Ensure the data directory exists (used by SQLite and ChromaDB)
Path("data").mkdir(exist_ok=True)

from database import Base, engine
from routers import auth, chat, knowledge_base

_COLUMN_MIGRATIONS = {
    "uploaded_files": {"summary": "ALTER TABLE uploaded_files ADD COLUMN summary TEXT"},
    "knowledge_bases": {"user_id": "ALTER TABLE knowledge_bases ADD COLUMN user_id INTEGER"},
    "conversations": {"thread_id": "ALTER TABLE conversations ADD COLUMN thread_id VARCHAR(64)"},
}
_CONVERSATION_THREAD_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS ix_conversations_thread_id_unique
ON conversations(thread_id)
WHERE thread_id IS NOT NULL
"""


def migrate_schema(bind) -> None:
    """Apply repeatable SQLite migrations without hiding unexpected errors."""
    with bind.begin() as connection:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        for table_name, columns in _COLUMN_MIGRATIONS.items():
            if table_name not in tables:
                continue
            existing = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, statement in columns.items():
                if column_name not in existing:
                    connection.execute(text(statement))
        if "conversations" in tables:
            connection.execute(text(_CONVERSATION_THREAD_INDEX))


# Auto-create all tables (new installs), then upgrade existing installs.
Base.metadata.create_all(bind=engine)
migrate_schema(engine)

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
