from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from auth import get_current_user
from database import Base, get_db
from models import Conversation
from routers import chat

TERMINAL_TOKENS = {"[DONE]", "[PAUSED]", "[FAILED]"}



def _app_and_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'chat-routes.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    app = FastAPI()
    app.include_router(chat.router)
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=7)
    return app, session


def _sse_payloads(response_text: str) -> list[object]:
    payloads = []
    for line in response_text.splitlines():
        if not line.startswith("data: "):
            continue
        data = line.removeprefix("data: ")
        if data in TERMINAL_TOKENS:
            payloads.append(data)
        else:
            payloads.append(json.loads(data))
    return payloads


def test_graph_direct_http_sse_persistence_and_history_are_identical(tmp_path) -> None:
    app, session = _app_and_session(tmp_path)
    try:
        with (
            patch.dict(
                "os.environ",
                {
                    "SMARTDESK_AGENT_BACKEND": "langgraph",
                    "SMARTDESK_HITL_WRITE_NOTE": "false",
                },
                clear=False,
            ),
            patch("routers.chat._owned_kb"),
            patch("agent.graph.route", return_value="direct"),
            patch("agent.graph.llm_stream", return_value=iter(["Hello ", "world."])),
        ):
            client = TestClient(app)
            response = client.post(
                "/api/chat/stream",
                json={"kb_id": 1, "message": "Hello"},
            )
            history = client.get("/api/chat/history/1")

        payloads = _sse_payloads(response.text)
        delivered = "".join(item for item in payloads if isinstance(item, str) and item not in TERMINAL_TOKENS)
        conversation = session.query(Conversation).one()

        assert response.status_code == 200
        assert payloads[-1] == "[DONE]"
        assert delivered == "Hello world."
        assert conversation.answer == delivered
        assert conversation.thread_id is not None
        assert history.json()[0]["answer"] == delivered
    finally:
        session.close()


def test_graph_rag_http_sources_delivery_persistence_and_history_are_identical(tmp_path) -> None:
    app, session = _app_and_session(tmp_path)
    results = [{
        "text": "MCP is a protocol.",
        "filename": "mcp.md",
        "chunk_index": 0,
        "distance": 0.1,
    }]
    try:
        with (
            patch.dict(
                "os.environ",
                {
                    "SMARTDESK_AGENT_BACKEND": "langgraph",
                    "SMARTDESK_HITL_WRITE_NOTE": "false",
                },
                clear=False,
            ),
            patch("routers.chat._owned_kb"),
            patch("agent.graph.route", return_value="rag"),
            patch("agent.graph.chroma_client.query_documents", return_value=results),
            patch("agent.graph.assess_rag_quality", return_value=True),
            patch(
                "agent.graph.generate_answer_stream",
                return_value=iter(["MCP answer.", "[SOURCE_USED]"]),
            ),
        ):
            client = TestClient(app)
            response = client.post(
                "/api/chat/stream",
                json={"kb_id": 1, "message": "What is MCP?"},
            )
            history = client.get("/api/chat/history/1")

        payloads = _sse_payloads(response.text)
        delivered = "".join(item for item in payloads if isinstance(item, str) and item not in TERMINAL_TOKENS)
        sources = next(item["sources"] for item in payloads if isinstance(item, dict) and "sources" in item)
        conversation = session.query(Conversation).one()

        assert response.status_code == 200
        assert payloads[-1] == "[DONE]"
        assert sources == [{
            "type": "document",
            "filename": "mcp.md",
            "preview": "MCP is a protocol.",
        }]
        assert delivered == "MCP answer."
        assert conversation.answer == delivered
        assert conversation.thread_id is not None
        assert history.json()[0]["answer"] == delivered
    finally:
        session.close()


def test_graph_direct_conflicting_thread_completion_fails(tmp_path) -> None:
    app, session = _app_and_session(tmp_path)
    try:
        with (
            patch.dict(
                "os.environ",
                {
                    "SMARTDESK_AGENT_BACKEND": "langgraph",
                    "SMARTDESK_HITL_WRITE_NOTE": "false",
                },
                clear=False,
            ),
            patch("routers.chat._owned_kb"),
            patch("agent.graph.route", return_value="direct"),
            patch("agent.graph.llm_stream", return_value=iter(["Hello."])),
            patch(
                "routers.chat.persist_conversation_once",
                side_effect=chat.ConversationThreadConflictError("conflict"),
            ),
        ):
            client = TestClient(app)
            with pytest.raises(chat.ConversationThreadConflictError, match="conflict"):
                client.post(
                    "/api/chat/stream",
                    json={"kb_id": 1, "message": "Hello"},
                )
    finally:
        session.close()
