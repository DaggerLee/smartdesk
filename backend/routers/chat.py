import json
import re
from typing import Generator, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

import chroma_client
from auth import get_current_user
from database import get_db
from gemini_client import generate_answer_stream
from models import Conversation, KnowledgeBase, User
from tools import assess_rag_quality, fetch_weather, is_weather_query, web_search

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


# ── Message classification ────────────────────────────────────────────────────

# Short acknowledgments and greetings — no document lookup needed
_CONVERSATIONAL_RE = re.compile(
    r"^(thanks?|thank you|thx|ok|okay|got it|understood|makes sense|great|cool|nice|"
    r"perfect|awesome|sure|alright|yep|yup|nope|"
    r"hi|hello|hey|bye|goodbye|"
    r"谢谢|谢了|好的|好|明白|了解|嗯|知道了|收到|没问题|可以|行|对|是的|"
    r"你好|哈喽|再见|👍|👌)[\s!?.。！？]*$",
    re.IGNORECASE,
)

# Follow-up words that reference previous context rather than a new topic
# Note: \b word boundaries do not work with Chinese characters, so Chinese patterns
# are listed separately without boundary anchors.
_FOLLOWUP_RE = re.compile(
    r"\b(that|it|this|those|them|above|the previous|the last|"
    r"tell me more|elaborate|more detail|more about|expand on|explain more|"
    r"can you explain|what do you mean|what does that mean)\b"
    r"|继续|更多|详细|展开|解释|说说|刚才|上面|再说|能不能再",
    re.IGNORECASE,
)

# Format / style / language instructions that should re-process the previous answer
_FORMAT_RE = re.compile(
    r"\b(shorter|longer|simpler|summarize|summary|bullet|table|list|rewrite|rephrase|"
    r"more concise|step by step|in points|"
    r"reply in|answer in|respond in|switch to|change.*language)\b"
    # Chinese: match "用X语/文回答" or standalone style words
    r"|用.{1,4}[语文]|换.{0,3}语言|切换.{0,3}语言"
    r"|简短|总结|列表|表格|重写|换一种|分点|分步|简洁",
    re.IGNORECASE,
)


def _classify(message: str) -> str:
    """Classify the user message to decide how to route it.

    Returns one of:
      'conversational' — greeting / acknowledgment, skip RAG entirely
      'meta'           — format or language instruction, skip RAG, re-process last answer
      'followup'       — references previous topic, use last question as RAG query
      'question'       — normal new question, full RAG pipeline
    """
    m = message.strip()
    if _CONVERSATIONAL_RE.match(m):
        return "conversational"
    if _FORMAT_RE.search(m):
        return "meta"
    if _FOLLOWUP_RE.search(m):
        return "followup"
    return "question"


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

    # Fetch the last 5 conversations for memory context (oldest first)
    recent_history = (
        db.query(Conversation)
        .filter(Conversation.kb_id == body.kb_id)
        .order_by(Conversation.created_at.desc())
        .limit(5)
        .all()
    )
    history = list(reversed(recent_history))

    # ── Classify message and decide RAG strategy ──────────────────────────────
    msg_type = _classify(body.message)
    print(f"[Chat] Message type: {msg_type!r} — {body.message!r}")

    context_texts: List[str] = []
    doc_sources: List[dict] = []
    web_results: List[dict] = []

    if msg_type == "conversational":
        # Greetings / acknowledgments: skip all retrieval, just respond naturally
        pass

    elif msg_type in ("meta", "followup") and history:
        # Meta (format/language change) or follow-up: search using the last real question
        # so we retrieve the same document chunks that were relevant before
        rag_query = history[-1].question
        results = chroma_client.query_documents(body.kb_id, rag_query, n_results=5)
        context_texts = [r["text"] for r in results]
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
        # No web search for meta/follow-up — the answer already exists in history

    else:
        # Normal question: full RAG + optional web search
        results = chroma_client.query_documents(body.kb_id, body.message, n_results=5)
        context_texts = [r["text"] for r in results]
        seen_files = set()
        for r in results:
            fname = r["filename"]
            if fname != "Unknown" and fname not in seen_files:
                seen_files.add(fname)
                doc_sources.append({
                    "type": "document",
                    "filename": fname,
                    "preview": r["text"][:80].replace("\n", " "),
                })

        if not assess_rag_quality(results):
            print(f"[Chat] RAG insufficient — triggering external tools")

            # Weather queries get real structured data first, then supplement with web search
            if is_weather_query(body.message):
                weather_summary = fetch_weather(body.message)
                if weather_summary:
                    web_results = [{"title": "Real-time weather data", "url": "", "snippet": weather_summary}]
                else:
                    web_results = web_search(body.message)
            else:
                web_results = web_search(body.message)

            print(f"[Chat] External tools returned {len(web_results)} results")

    def generate() -> Generator[str, None, None]:
        chunks: List[str] = []
        for chunk in generate_answer_stream(
            body.message, context_texts, history, web_results or None, msg_type
        ):
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
