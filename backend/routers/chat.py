import hashlib
import json
import os
import re
import uuid
from typing import Generator, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import chroma_client
from agent.action_locks import action_lock
from agent.delivery import (
    NON_CONTEXT_ANSWERS,
    is_verified_delivery_enabled,
    select_delivery,
)
from agent.graph import (
    GraphEvent,
    get_graph_snapshot,
    resume_graph_action,
    stream_graph,
)
from agent.loop import SYSTEM_PROMPT, run_agent
from agent.router import route
from agent.write_action import ActionResolution
from auth import get_current_user
from database import SessionLocal, get_db
from gemini_client import generate_answer_stream
from llm.client import stream as llm_stream
from llm.trace import (
    context as _trace_context,
    iterate_with_context as _trace_iter,
    write as _trace_write,
)
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


def _sse_json(payload) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

def _stream_graph_with_proposal_failure(*args, **kwargs):
    try:
        yield from stream_graph(*args, **kwargs)
    except Exception:
        query = args[0]
        if is_hitl_write_note_enabled() and classify_write_intent(query) == "persist":
            yield GraphEvent(type="proposal_failed", data={})
            return
        raise

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


def _recent_usable_history(db: Session, kb_id: int) -> list[Conversation]:
    """Load five usable turns; fixed delivery notices never enter context."""
    return (
        db.query(Conversation)
        .filter(
            Conversation.kb_id == kb_id,
            Conversation.answer.notin_(tuple(sorted(NON_CONTEXT_ANSWERS))),
        )
        .order_by(Conversation.created_at.desc())
        .limit(5)
        .all()
    )


def _answer_sha256(answer: str) -> str:
    return hashlib.sha256(answer.encode("utf-8")).hexdigest()


class ConversationThreadConflictError(RuntimeError):
    """A thread ID is already bound to a different delivered conversation."""


def _same_conversation(
    conversation: Conversation,
    *,
    kb_id: int,
    question: str,
    answer: str,
) -> bool:
    return (
        conversation.kb_id == kb_id
        and conversation.question == question
        and conversation.answer == answer
    )


def persist_conversation_once(
    db: Session,
    *,
    thread_id: str,
    kb_id: int,
    question: str,
    answer: str,
) -> Conversation:
    """Insert one delivered graph conversation or verify an exact replay."""
    existing = db.query(Conversation).filter(
        Conversation.thread_id == thread_id
    ).one_or_none()
    if existing is not None:
        if _same_conversation(existing, kb_id=kb_id, question=question, answer=answer):
            return existing
        raise ConversationThreadConflictError("thread already has a different conversation")

    conversation = Conversation(
        kb_id=kb_id,
        question=question,
        answer=answer,
        thread_id=thread_id,
    )
    db.add(conversation)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        existing = db.query(Conversation).filter(
            Conversation.thread_id == thread_id
        ).one_or_none()
        if existing is None:
            raise error
        if not _same_conversation(existing, kb_id=kb_id, question=question, answer=answer):
            raise ConversationThreadConflictError(
                "thread already has a different conversation"
            ) from None
        return existing
    db.refresh(conversation)
    return conversation


def _resolution_matches(pending: dict, resolution: ActionResolution) -> bool:
    if pending.get("action_id") != resolution.action_id:
        return False
    if pending.get("decision") != resolution.decision:
        return False
    if resolution.decision == "approve":
        return pending.get("approved_payload") == pending.get("original_payload")
    if resolution.decision == "edit":
        return pending.get("approved_payload") == {
            "title": resolution.title,
            "content": resolution.content,
        }
    return pending.get("reject_reason") == resolution.reason


def _action_frames(
    thread_id: str,
    resolution: ActionResolution,
    current_user_id: int,
) -> list[str]:
    with action_lock(thread_id):
        snapshot = get_graph_snapshot(thread_id)
        pending = snapshot.get("pending_action") if snapshot else None
        if not pending or pending.get("user_id") != current_user_id:
            raise HTTPException(status_code=404, detail="Action not found")
        if pending.get("action_id") != resolution.action_id:
            raise HTTPException(status_code=404, detail="Action not found")

        frames: list[str] = []
        receipt = pending.get("receipt")
        if receipt:
            if not _resolution_matches(pending, resolution):
                raise HTTPException(status_code=409, detail="Action resolution conflicts")
            frames.append(_sse_json({"action_result": receipt}))
            final_state = snapshot
        else:
            try:
                final_state = None
                for event in resume_graph_action(thread_id, resolution):
                    if event.type == "final":
                        final_state = event.data
                committed = get_graph_snapshot(thread_id)
                committed_receipt = (
                    committed.get("pending_action", {}).get("receipt")
                    if committed
                    else None
                )
                if committed_receipt is None:
                    raise RuntimeError("action result checkpoint is unavailable")
                frames.append(_sse_json({"action_result": committed_receipt}))
                final_state = committed
            except Exception:
                frames.append(_sse_json({"error": {"stage": "action_result"}}))
                frames.append("data: [FAILED]\n\n")
                return frames

        answer = final_state.get("answer", "")
        if final_state.get("verification_source") != "action_receipt" or not answer:
            frames.append(_sse_json({"error": {"stage": "action_result"}}))
            frames.append("data: [FAILED]\n\n")
            return frames

        db = SessionLocal()
        try:
            persist_conversation_once(
                db,
                thread_id=thread_id,
                kb_id=final_state["kb_id"],
                question=final_state["query"],
                answer=answer,
            )
        except ConversationThreadConflictError as error:
            raise HTTPException(
                status_code=409, detail="Conversation completion conflicts"
            ) from error
        except Exception:
            frames.append(_sse_json({"error": {"stage": "conversation"}}))
            frames.append("data: [FAILED]\n\n")
            return frames
        finally:
            db.close()

        frames.append(_sse_json(answer))
        frames.append("data: [DONE]\n\n")
        return frames


@router.post("/actions/{thread_id}/resolve")
def resolve_action(
    thread_id: str,
    resolution: ActionResolution,
    current_user: User = Depends(get_current_user),
):
    frames = _action_frames(thread_id, resolution, current_user.id)
    return StreamingResponse(
        iter(frames),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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

    request_id = uuid.uuid4().hex[:12]
    _sse_headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

    # ── LangGraph skeleton switch (default off — see agent/graph.py) ─────────
    # Off by default so production behaviour is byte-for-byte unchanged; the
    # legacy route()/run_agent()/inline-RAG-chain path below is the fallback.
    if os.getenv("SMARTDESK_AGENT_BACKEND") == "langgraph":
        def generate_langgraph() -> Generator[str, None, None]:
            with _trace_context(request_id=request_id):
                # W5 T4: generated and held at the call site (not left to
                # stream_graph()'s auto-generate fallback) so a crashed run's
                # thread_id is recoverable from logs for a manual
                # agent.graph.resume_graph(thread_id) call — the actual
                # foundation this task lays for HITL resume. Deliberately not
                # request_id: their lifetimes diverge (a future resume
                # request gets its own fresh request_id but must reuse this
                # thread_id) — see agent/graph.py's module docstring.
                thread_id = uuid.uuid4().hex
                print(f"[Chat] LangGraph thread_id={thread_id}")
                recent_history = _recent_usable_history(db, body.kb_id)
                final_state: dict = {}
                for event in _stream_graph_with_proposal_failure(
                    body.message,
                    body.kb_id,
                    history=list(reversed(recent_history)),
                    thread_id=thread_id,
                    user_id=current_user.id,
                ):
                    if event.type == "tool_call":
                        name = event.data["name"]
                        label = "Searching knowledge base…" if name == "retrieve" else "Searching the web…"
                        yield f"data: {json.dumps({'status': label}, ensure_ascii=False)}\n\n"
                    elif event.type == "chunk":
                        yield f"data: {json.dumps(event.data['text'], ensure_ascii=False)}\n\n"
                    elif event.type == "confirmation_required":
                        yield _sse_json(
                            {"confirmation_required": {"thread_id": thread_id, **event.data}}
                        )
                        yield "data: [PAUSED]\n\n"
                        return
                    elif event.type == "proposal_failed":
                        yield _sse_json({"error": {"stage": "proposal"}})
                        yield "data: [FAILED]\n\n"
                        return
                    elif event.type == "final":
                        final_state = event.data

                route_taken = final_state.get("route")
                answer = final_state.get("answer", "")

                if route_taken == "agent":
                    graph_answer = answer
                    verification_status = final_state.get("verification_status")
                    feature_enabled = is_verified_delivery_enabled()

                    if feature_enabled:
                        decision = select_delivery(graph_answer, verification_status)
                        answer = decision.payload
                        delivery_kind = decision.kind
                        post_graph_generation_calls = 0
                    else:
                        chunks: List[str] = []
                        final_msgs = final_state.get("messages")
                        if final_msgs:
                            for chunk in llm_stream(final_msgs, system=SYSTEM_PROMPT):
                                chunks.append(chunk)
                                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                        answer = "".join(chunks)
                        delivery_kind = "regenerated_answer"
                        post_graph_generation_calls = 1

                    _db = SessionLocal()
                    try:
                        _db.add(Conversation(kb_id=body.kb_id, question=body.message, answer=answer))
                        _db.commit()
                    finally:
                        _db.close()

                    graph_hash = _answer_sha256(graph_answer)
                    persisted_hash = _answer_sha256(answer)
                    _trace_write({
                        "type": "agent_delivery",
                        "thread_id": thread_id,
                        "verification_status": verification_status,
                        "feature_enabled": feature_enabled,
                        "delivery_kind": delivery_kind,
                        "graph_answer_sha256": graph_hash,
                        "persisted_payload_sha256": persisted_hash,
                        "graph_answer_matches_persisted": graph_hash == persisted_hash,
                        "post_graph_generation_calls": post_graph_generation_calls,
                        "persisted_payload_chars": len(answer),
                    })

                    if feature_enabled:
                        yield f"data: {json.dumps(answer, ensure_ascii=False)}\n\n"

                else:
                    if route_taken == "rag":
                        doc_sources = final_state.get("doc_sources") or []
                        web_results = final_state.get("web_results") or []
                        all_sources: List[dict] = []
                        if final_state.get("used_docs") and doc_sources:
                            all_sources.extend(doc_sources)
                        if final_state.get("used_web") and web_results:
                            for r in web_results:
                                all_sources.append({
                                    "type": "web",
                                    "title": r.get("title", "Web result"),
                                    "url": r.get("url", ""),
                                    "snippet": r.get("snippet", ""),
                                })
                        if all_sources:
                            yield f"data: {json.dumps({'sources': all_sources}, ensure_ascii=False)}\n\n"

                    db.add(Conversation(kb_id=body.kb_id, question=body.message, answer=answer))
                    db.commit()

                yield "data: [DONE]\n\n"
        return StreamingResponse(
            _trace_iter(generate_langgraph(), request_id=request_id),
            media_type="text/event-stream",
            headers=_sse_headers,
        )

    with _trace_context(request_id=request_id):
        path = route(body.message)
    print(f"[Chat] Route: {path!r} — {body.message!r}")

    # ── direct: conversational reply, no retrieval ────────────────────────────
    if path == "direct":
        def generate_direct() -> Generator[str, None, None]:
            with _trace_context(request_id=request_id):
                msgs = [{"role": "user", "parts": [{"text": body.message}]}]
                chunks: List[str] = []
                for chunk in llm_stream(msgs):
                    chunks.append(chunk)
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                conv = Conversation(kb_id=body.kb_id, question=body.message, answer="".join(chunks))
                db.add(conv)
                db.commit()
                yield "data: [DONE]\n\n"
        return StreamingResponse(
            _trace_iter(generate_direct(), request_id=request_id),
            media_type="text/event-stream",
            headers=_sse_headers,
        )

    # ── agent: multi-turn tool loop ───────────────────────────────────────────
    if path == "agent":
        def generate_agent() -> Generator[str, None, None]:
            with _trace_context(request_id=request_id):
                final_msgs: Optional[List[dict]] = None
                for event in run_agent(body.message, body.kb_id):
                    if event.type == "tool_call":
                        name = event.data["name"]
                        label = "Searching knowledge base…" if name == "retrieve" else "Searching the web…"
                        yield f"data: {json.dumps({'status': label}, ensure_ascii=False)}\n\n"
                    elif event.type == "final":
                        final_msgs = event.data.get("messages")

                chunks: List[str] = []
                if final_msgs:
                    for chunk in llm_stream(final_msgs, system=SYSTEM_PROMPT):
                        chunks.append(chunk)
                        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

                _db = SessionLocal()
                try:
                    _db.add(Conversation(
                        kb_id=body.kb_id, question=body.message, answer="".join(chunks)
                    ))
                    _db.commit()
                finally:
                    _db.close()
                yield "data: [DONE]\n\n"
        return StreamingResponse(
            _trace_iter(generate_agent(), request_id=request_id),
            media_type="text/event-stream",
            headers=_sse_headers,
        )

    # ── rag: v1 existing chain (unchanged) ───────────────────────────────────
    # Fetch the last 5 conversations for memory context (oldest first)
    recent_history = _recent_usable_history(db, body.kb_id)
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
        with _trace_context(request_id=request_id):
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
        _trace_iter(generate(), request_id=request_id),
        media_type="text/event-stream",
        headers=_sse_headers,
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
from agent.write_note_policy import (
    classify_write_intent,
    is_hitl_write_note_enabled,
)
