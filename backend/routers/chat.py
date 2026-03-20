from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

import chroma_client
from database import get_db
from gemini_client import generate_answer
from models import Conversation, KnowledgeBase

router = APIRouter(prefix="/api/chat", tags=["chat"])


# ── Pydantic Schemas ──────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    kb_id: int
    message: str


class ChatResponse(BaseModel):
    answer: str
    sources: List[str]


class HistoryItem(BaseModel):
    id: int
    question: str
    answer: str
    created_at: str


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("", response_model=ChatResponse)
def chat(body: ChatRequest, db: Session = Depends(get_db)):
    kb = db.query(KnowledgeBase).filter(KnowledgeBase.id == body.kb_id).first()
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")

    if not body.message.strip():
        raise HTTPException(status_code=400, detail="消息不能为空")

    # 1. 从 ChromaDB 检索相关文档块
    context = chroma_client.query_documents(body.kb_id, body.message, n_results=5)

    # 2. 调用 Gemini 生成回答
    answer = generate_answer(body.message, context)

    # 3. 保存对话记录
    conv = Conversation(kb_id=body.kb_id, question=body.message, answer=answer)
    db.add(conv)
    db.commit()

    return ChatResponse(answer=answer, sources=context[:3])


@router.get("/history/{kb_id}", response_model=List[HistoryItem])
def get_history(kb_id: int, db: Session = Depends(get_db)):
    kb = db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")

    convs = (
        db.query(Conversation)
        .filter(Conversation.kb_id == kb_id)
        .order_by(Conversation.created_at.asc())
        .all()
    )

    return [
        HistoryItem(
            id=c.id,
            question=c.question,
            answer=c.answer,
            created_at=c.created_at.strftime("%Y-%m-%d %H:%M:%S") if c.created_at else "",
        )
        for c in convs
    ]


@router.delete("/history/{kb_id}")
def clear_history(kb_id: int, db: Session = Depends(get_db)):
    db.query(Conversation).filter(Conversation.kb_id == kb_id).delete()
    db.commit()
    return {"message": "对话历史已清空"}
