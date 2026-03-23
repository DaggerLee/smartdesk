import io
import uuid
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

import chroma_client
from auth import get_current_user
from database import SessionLocal, get_db
from gemini_client import generate_summary
from models import Conversation, KnowledgeBase, UploadedFile, User

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
    summary: Optional[str] = None

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
        for enc in ("utf-8", "gbk", "latin-1"):
            try:
                return content.decode(enc)
            except UnicodeDecodeError:
                continue
        raise HTTPException(status_code=400, detail="Unrecognized file encoding. Please use UTF-8 or GBK.")


def _owned_kb(kb_id: int, user_id: int, db: Session) -> KnowledgeBase:
    """Fetch a KB and verify it belongs to the current user; raise 404 otherwise."""
    kb = db.query(KnowledgeBase).filter(
        KnowledgeBase.id == kb_id, KnowledgeBase.user_id == user_id
    ).first()
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    return kb


def _generate_and_store_summary(file_id: int, text: str) -> None:
    """Background task: generate a summary and persist it to the DB."""
    summary = generate_summary(text)
    if not summary:
        return
    db = SessionLocal()
    try:
        record = db.query(UploadedFile).filter(UploadedFile.id == file_id).first()
        if record:
            record.summary = summary
            db.commit()
    finally:
        db.close()


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("", response_model=KBResponse)
def create_knowledge_base(
    body: KBCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    kb = KnowledgeBase(name=body.name, description=body.description or "", user_id=current_user.id)
    db.add(kb)
    db.commit()
    db.refresh(kb)
    return _to_kb_response(kb)


@router.get("", response_model=List[KBResponse])
def list_knowledge_bases(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    kbs = (
        db.query(KnowledgeBase)
        .filter(KnowledgeBase.user_id == current_user.id)
        .order_by(KnowledgeBase.created_at.desc())
        .all()
    )
    return [_to_kb_response(kb) for kb in kbs]


@router.delete("/{kb_id}")
def delete_knowledge_base(
    kb_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    kb = _owned_kb(kb_id, current_user.id, db)
    chroma_client.delete_collection(kb_id)
    db.query(UploadedFile).filter(UploadedFile.kb_id == kb_id).delete()
    db.query(Conversation).filter(Conversation.kb_id == kb_id).delete()
    db.delete(kb)
    db.commit()
    return {"message": "Deleted successfully"}


@router.get("/{kb_id}/files", response_model=List[FileResponse])
def list_files(
    kb_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _owned_kb(kb_id, current_user.id, db)
    files = (
        db.query(UploadedFile)
        .filter(UploadedFile.kb_id == kb_id)
        .order_by(UploadedFile.uploaded_at.desc())
        .all()
    )
    return [_to_file_response(f) for f in files]


@router.delete("/{kb_id}/files/{filename}")
def delete_file(
    kb_id: int,
    filename: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _owned_kb(kb_id, current_user.id, db)
    record = (
        db.query(UploadedFile)
        .filter(UploadedFile.kb_id == kb_id, UploadedFile.filename == filename)
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="File not found")
    chroma_client.delete_documents_by_filename(kb_id, filename)
    db.delete(record)
    db.commit()
    return {"message": "File deleted successfully"}


@router.post("/{kb_id}/upload")
async def upload_file(
    kb_id: int,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _owned_kb(kb_id, current_user.id, db)

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

    prefix = filename.replace(" ", "_")[:30]
    ids = [f"{prefix}_{uuid.uuid4().hex[:8]}_{i}" for i, _ in enumerate(chunks)]
    metadatas = [{"filename": filename, "chunk_index": i} for i, _ in enumerate(chunks)]
    chroma_client.add_documents(kb_id, chunks, ids, metadatas)

    existing = (
        db.query(UploadedFile)
        .filter(UploadedFile.kb_id == kb_id, UploadedFile.filename == filename)
        .first()
    )
    if existing:
        existing.chunk_count = len(chunks)
        existing.summary = None
        from datetime import datetime
        existing.uploaded_at = datetime.utcnow()
        db.commit()
        file_id = existing.id
    else:
        record = UploadedFile(kb_id=kb_id, filename=filename, chunk_count=len(chunks))
        db.add(record)
        db.commit()
        db.refresh(record)
        file_id = record.id

    background_tasks.add_task(_generate_and_store_summary, file_id, text)

    return {"message": "File uploaded and parsed successfully", "filename": filename, "chunks": len(chunks)}


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
        summary=f.summary,
    )
