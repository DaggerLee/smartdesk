import io
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

import chroma_client
from database import get_db
from models import Conversation, KnowledgeBase, UploadedFile

router = APIRouter(prefix="/api/knowledge-base", tags=["knowledge-base"])


# ── Pydantic Schemas ──────────────────────────────────────────────────────────

class KBCreate(BaseModel):
    name: str
    description: Optional[str] = ""


class KBResponse(BaseModel):
    id: int
    name: str
    description: str
    created_at: str

    model_config = {"from_attributes": True}


class FileResponse(BaseModel):
    id: int
    filename: str
    chunk_count: int
    uploaded_at: str

    model_config = {"from_attributes": True}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_text(filename: str, content: bytes) -> str:
    """Extract plain text from a PDF or text file."""
    if filename.lower().endswith(".pdf"):
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(content))
            pages = [page.extract_text() or "" for page in reader.pages]
            return "\n\n".join(pages)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"PDF parsing failed: {e}")
    else:
        # Try UTF-8 first, then GBK, then latin-1 as fallback
        for enc in ("utf-8", "gbk", "latin-1"):
            try:
                return content.decode(enc)
            except UnicodeDecodeError:
                continue
        raise HTTPException(status_code=400, detail="Unrecognized file encoding. Please use UTF-8 or GBK.")


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("", response_model=KBResponse)
def create_knowledge_base(body: KBCreate, db: Session = Depends(get_db)):
    kb = KnowledgeBase(name=body.name, description=body.description or "")
    db.add(kb)
    db.commit()
    db.refresh(kb)
    return _to_kb_response(kb)


@router.get("", response_model=List[KBResponse])
def list_knowledge_bases(db: Session = Depends(get_db)):
    kbs = db.query(KnowledgeBase).order_by(KnowledgeBase.created_at.desc()).all()
    return [_to_kb_response(kb) for kb in kbs]


@router.delete("/{kb_id}")
def delete_knowledge_base(kb_id: int, db: Session = Depends(get_db)):
    kb = db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    # Delete ChromaDB collection (identified by kb_id, not name)
    chroma_client.delete_collection(kb_id)
    # Delete all associated SQLite records
    db.query(UploadedFile).filter(UploadedFile.kb_id == kb_id).delete()
    db.query(Conversation).filter(Conversation.kb_id == kb_id).delete()
    db.delete(kb)
    db.commit()
    return {"message": "Deleted successfully"}


@router.get("/{kb_id}/files", response_model=List[FileResponse])
def list_files(kb_id: int, db: Session = Depends(get_db)):
    kb = db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    files = (
        db.query(UploadedFile)
        .filter(UploadedFile.kb_id == kb_id)
        .order_by(UploadedFile.uploaded_at.desc())
        .all()
    )
    return [_to_file_response(f) for f in files]


@router.delete("/{kb_id}/files/{filename}")
def delete_file(kb_id: int, filename: str, db: Session = Depends(get_db)):
    kb = db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    record = (
        db.query(UploadedFile)
        .filter(UploadedFile.kb_id == kb_id, UploadedFile.filename == filename)
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="File not found")
    # Remove all ChromaDB chunks that belong to this file
    chroma_client.delete_documents_by_filename(kb_id, filename)
    db.delete(record)
    db.commit()
    return {"message": "File deleted successfully"}


@router.post("/{kb_id}/upload")
async def upload_file(
    kb_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    kb = db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    filename = file.filename or "upload"
    if not (filename.lower().endswith(".pdf") or filename.lower().endswith(".txt")):
        raise HTTPException(status_code=400, detail="Only PDF and TXT files are supported")

    content = await file.read()
    text = _extract_text(filename, content)

    if not text.strip():
        raise HTTPException(status_code=400, detail="File is empty or could not be parsed")

    chunks = chroma_client.chunk_text(text)
    if not chunks:
        raise HTTPException(status_code=400, detail="Text chunking failed — please check the file content")

    # Generate unique IDs: filename prefix + UUID
    prefix = filename.replace(" ", "_")[:30]
    ids = [f"{prefix}_{uuid.uuid4().hex[:8]}_{i}" for i, _ in enumerate(chunks)]
    metadatas = [{"filename": filename, "chunk_index": i} for i, _ in enumerate(chunks)]

    chroma_client.add_documents(kb_id, chunks, ids, metadatas)

    # Record the upload in SQLite (upsert: replace existing record for same filename)
    existing = (
        db.query(UploadedFile)
        .filter(UploadedFile.kb_id == kb_id, UploadedFile.filename == filename)
        .first()
    )
    if existing:
        existing.chunk_count = len(chunks)
        from datetime import datetime
        existing.uploaded_at = datetime.utcnow()
    else:
        db.add(UploadedFile(kb_id=kb_id, filename=filename, chunk_count=len(chunks)))
    db.commit()

    return {
        "message": "File uploaded and parsed successfully",
        "filename": filename,
        "chunks": len(chunks),
    }


# ── Util ──────────────────────────────────────────────────────────────────────

def _to_kb_response(kb: KnowledgeBase) -> KBResponse:
    return KBResponse(
        id=kb.id,
        name=kb.name,
        description=kb.description or "",
        created_at=kb.created_at.strftime("%Y-%m-%d %H:%M:%S") if kb.created_at else "",
    )


def _to_file_response(f: UploadedFile) -> FileResponse:
    return FileResponse(
        id=f.id,
        filename=f.filename,
        chunk_count=f.chunk_count,
        uploaded_at=f.uploaded_at.strftime("%Y-%m-%d %H:%M:%S") if f.uploaded_at else "",
    )
