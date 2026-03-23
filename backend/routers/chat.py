import json
from typing import Generator, List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

import chroma_client
from auth import get_current_user
from database import get_db
from gemini_client import generate_answer_stream
from models import Conversation, KnowledgeBase, User
from tools import assess_rag_quality, web_search

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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _owned_kb(kb_id: int, user_id: int, db: Session) -> KnowledgeBase:
    kb = db.query(KnowledgeBase).filter(
        KnowledgeBase.id == kb_id, KnowledgeBase.user_id == user_id
    ).first()
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    return kb


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/stream")
def chat_stream(
    body: ChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _owned_kb(body.kb_id, current_user.id, db)

    if not body.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    # Fetch the last 5 conversations for memory context
    recent_history = (
        db.query(Conversation)
        .filter(Conversation.kb_id == body.kb_id)
        .order_by(Conversation.created_at.desc())
        .limit(5)
        .all()
    )
    history = list(reversed(recent_history))

    results = chroma_client.query_documents(body.kb_id, body.message, n_results=5)
    context_texts = [r["text"] for r in results]

    # Build deduplicated document sources list
    doc_sources: List[dict] = []
    seen_files: set = set()
    for r in results:
        fname = r["filename"]
        if fname != "Unknown" and fname not in seen_files:
            seen_files.add(fname)
            doc_sources.append({
                "type": "document",
                "filename": fname,
                "preview": r["text"][:80].replace("\n", " "),
            })

    # Supplement with web search when document context is insufficient
    rag_sufficient = assess_rag_quality(results)
    web_results: List[dict] = [] if rag_sufficient else web_search(body.message)

    def generate() -> Generator[str, None, None]:
        chunks: List[str] = []
        for chunk in generate_answer_stream(body.message, context_texts, history, web_results or None):
            chunks.append(chunk)
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

        full_answer = "".join(chunks)
        used_docs = "[SOURCE_USED]" in full_answer
        used_web = "[WEB_USED]" in full_answer
        clean_answer = full_answer.replace("[SOURCE_USED]", "").replace("[WEB_USED]", "").rstrip()

        all_sources: List[dict] = []
        if used_docs and doc_sources:
            all_sources.extend(doc_sources)
        if used_web and web_results:
            for r in web_results:
                all_sources.append({
                    "type": "web",
                    "title": r.get("title", "Web result"),
                    "url": r.get("url", ""),
                    "snippet": r.get("snippet", ""),
                })

        if all_sources:
            yield f"data: {json.dumps({'sources': all_sources}, ensure_ascii=False)}\n\n"

        conv = Conversation(kb_id=body.kb_id, question=body.message, answer=clean_answer)
        db.add(conv)
        db.commit()
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/history/{kb_id}", response_model=List[HistoryItem])
def get_history(
    kb_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _owned_kb(kb_id, current_user.id, db)
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
def clear_history(
    kb_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _owned_kb(kb_id, current_user.id, db)
    db.query(Conversation).filter(Conversation.kb_id == kb_id).delete()
    db.commit()
    return {"message": "Chat history cleared"}
