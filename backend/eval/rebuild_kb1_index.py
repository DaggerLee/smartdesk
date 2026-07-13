#!/usr/bin/env python3
"""backend/eval/rebuild_kb1_index.py — W4 fix step 4: rebuild kb_id=1's
ChromaDB collection under the new multilingual embedding function and expand
the corpus to all four docs-local/notes/*.html files.

Why a standalone script instead of the /upload REST endpoint: the upload
endpoint only accepts .pdf/.txt (`Only PDF and TXT files are supported`);
the existing kb_1 content (Agentic_AI_Distilled_Notes.html, 11 chunks) was
seeded out-of-band the same way, via direct chroma_client calls with
BeautifulSoup tag-stripped text — this script follows that same pattern for
all four files instead of introducing a new path.

Must run BEFORE chroma_client's embedding function changes are exercised
elsewhere: switching SentenceTransformerEmbeddingFunction on an existing
ONNX-embedded collection raises a hard conflict error from ChromaDB (verified
empirically), so the collection is dropped and recreated, not patched in place.

Usage (from backend/):
    python3 eval/rebuild_kb1_index.py
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bs4 import BeautifulSoup

import chroma_client
from database import SessionLocal
from models import UploadedFile

KB_ID = 1
NOTES_DIR = Path(__file__).parent.parent.parent / "docs-local" / "notes"
NOTE_FILES = [
    "Agentic_AI_Distilled_Notes.html",
    "TinaHuang_AI_Distilled_Notes.html",
    "VibeCoding101_Distilled_Notes.html",
    "MCP_Distilled_Notes.html",
]


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return soup.get_text(separator=" ", strip=True)


def main() -> None:
    print(f"Dropping existing kb_{KB_ID} collection…")
    chroma_client.delete_collection(KB_ID)

    total_chunks = 0
    db = SessionLocal()
    try:
        db.query(UploadedFile).filter(UploadedFile.kb_id == KB_ID).delete()

        for filename in NOTE_FILES:
            path = NOTES_DIR / filename
            if not path.exists():
                print(f"  ✗ {filename}: not found at {path}, skipping")
                continue

            text = _html_to_text(path.read_text(encoding="utf-8"))
            if not text.strip():
                print(f"  ✗ {filename}: extracted empty text, skipping")
                continue

            chunks = chroma_client.chunk_text(text)
            prefix = filename.replace(" ", "_")[:30]
            ids = [f"{prefix}_{uuid.uuid4().hex[:8]}_{i}" for i in range(len(chunks))]
            metadatas = [{"filename": filename, "chunk_index": i} for i in range(len(chunks))]
            chroma_client.add_documents(KB_ID, chunks, ids, metadatas)

            db.add(UploadedFile(
                kb_id=KB_ID, filename=filename, chunk_count=len(chunks),
                uploaded_at=datetime.utcnow(),
            ))
            total_chunks += len(chunks)
            print(f"  ✓ {filename}: {len(chunks)} chunks")

        db.commit()
    finally:
        db.close()

    collection = chroma_client._get_or_create(KB_ID)
    print(f"\nRebuild complete: {total_chunks} chunks across {len(NOTE_FILES)} files")
    print(f"Collection count (verified): {collection.count()}")


if __name__ == "__main__":
    main()
