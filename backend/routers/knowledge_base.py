import io
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

import chroma_client
from database import get_db
from models import KnowledgeBase

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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_text(filename: str, content: bytes) -> str:
    """从 PDF 或纯文本文件中提取文字。"""
    if filename.lower().endswith(".pdf"):
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(content))
            pages = [page.extract_text() or "" for page in reader.pages]
            return "\n\n".join(pages)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"PDF 解析失败: {e}")
    else:
        # 尝试 UTF-8，再尝试 GBK
        for enc in ("utf-8", "gbk", "latin-1"):
            try:
                return content.decode(enc)
            except UnicodeDecodeError:
                continue
        raise HTTPException(status_code=400, detail="文件编码无法识别，请使用 UTF-8 或 GBK 编码的文本文件。")


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("", response_model=KBResponse)
def create_knowledge_base(body: KBCreate, db: Session = Depends(get_db)):
    kb = KnowledgeBase(name=body.name, description=body.description or "")
    db.add(kb)
    db.commit()
    db.refresh(kb)
    return _to_response(kb)


@router.get("", response_model=List[KBResponse])
def list_knowledge_bases(db: Session = Depends(get_db)):
    kbs = db.query(KnowledgeBase).order_by(KnowledgeBase.created_at.desc()).all()
    return [_to_response(kb) for kb in kbs]


@router.delete("/{kb_id}")
def delete_knowledge_base(kb_id: int, db: Session = Depends(get_db)):
    kb = db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")
    chroma_client.delete_collection(kb_id)
    db.delete(kb)
    db.commit()
    return {"message": "删除成功"}


@router.post("/{kb_id}/upload")
async def upload_file(
    kb_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    kb = db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")

    filename = file.filename or "upload"
    if not (filename.lower().endswith(".pdf") or filename.lower().endswith(".txt")):
        raise HTTPException(status_code=400, detail="仅支持 PDF 和 TXT 文件")

    content = await file.read()
    text = _extract_text(filename, content)

    if not text.strip():
        raise HTTPException(status_code=400, detail="文件内容为空，无法解析")

    chunks = chroma_client.chunk_text(text)
    if not chunks:
        raise HTTPException(status_code=400, detail="文本分块失败，请检查文件内容")

    # 生成唯一 ID：文件名前缀 + UUID
    prefix = filename.replace(" ", "_")[:30]
    ids = [f"{prefix}_{uuid.uuid4().hex[:8]}_{i}" for i, _ in enumerate(chunks)]

    chroma_client.add_documents(kb_id, chunks, ids)

    return {
        "message": "文件上传并解析成功",
        "filename": filename,
        "chunks": len(chunks),
    }


# ── Util ──────────────────────────────────────────────────────────────────────

def _to_response(kb: KnowledgeBase) -> KBResponse:
    return KBResponse(
        id=kb.id,
        name=kb.name,
        description=kb.description or "",
        created_at=kb.created_at.strftime("%Y-%m-%d %H:%M:%S") if kb.created_at else "",
    )
