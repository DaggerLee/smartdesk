import json
from typing import Generator, List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

import chroma_client
from database import get_db
from gemini_client import generate_answer_stream
from models import Conversation, KnowledgeBase

router = APIRouter(prefix="/api/chat", tags=["chat"])


# ── Pydantic Schemas ──────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    kb_id: int
    message: str


class HistoryItem(BaseModel):
    id: int
    question: str
    answer: str
    created_at: str


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/stream")
def chat_stream(body: ChatRequest, db: Session = Depends(get_db)):
    kb = db.query(KnowledgeBase).filter(KnowledgeBase.id == body.kb_id).first()
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    if not body.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    results = chroma_client.query_documents(body.kb_id, body.message, n_results=5)
    context_texts = [r["text"] for r in results]
    sources = [
        {"filename": r["filename"], "preview": r["text"][:80].replace("\n", " ")}
        for r in results
        if r["filename"] != "Unknown"
    ]
    # Deduplicate sources by filename while preserving order
    seen: set = set()
    unique_sources = []
    for s in sources:
        if s["filename"] not in seen:
            seen.add(s["filename"])
            unique_sources.append(s)

    def generate() -> Generator[str, None, None]:
        chunks: List[str] = []
        for chunk in generate_answer_stream(body.message, context_texts):
            chunks.append(chunk)
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        # Send sources before [DONE]
        if unique_sources:
            yield f"data: {json.dumps({'sources': unique_sources}, ensure_ascii=False)}\n\n"
        # Save complete answer to DB after streaming finishes
        full_answer = "".join(chunks)
        conv = Conversation(kb_id=body.kb_id, question=body.message, answer=full_answer)
        db.add(conv)
        db.commit()
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/history/{kb_id}", response_model=List[HistoryItem])
def get_history(kb_id: int, db: Session = Depends(get_db)):
    kb = db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

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
    return {"message": "Chat history cleared"}
