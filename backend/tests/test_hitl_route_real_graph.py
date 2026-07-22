from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import get_current_user
from llm.client import LLMResponse, ToolCall
from routers import chat


def test_real_graph_http_approve_writes_summary_and_delivers_receipt_only(
    tmp_path,
) -> None:
    thread_id = f"route-{uuid.uuid4().hex}"
    proposal = LLMResponse(
        text=None,
        tool_calls=[
            ToolCall(
                name="write_note",
                args={"title": "Summary", "content": "Deterministic summary body."},
            )
        ],
        raw={},
    )
    body = chat.ChatRequest(
        kb_id=3,
        message="Summarize X and save it as a Markdown note",
    )

    with (
        patch.dict(
            "os.environ",
            {
                "SMARTDESK_AGENT_BACKEND": "langgraph",
                "SMARTDESK_HITL_WRITE_NOTE": "true",
            },
            clear=False,
        ),
        patch("routers.chat._owned_kb"),
        patch("routers.chat._recent_usable_history", return_value=[]),
        patch("routers.chat.uuid.uuid4", return_value=SimpleNamespace(hex=thread_id)),
        patch("agent.graph.route", return_value="agent"),
        patch("agent.graph.complete", return_value=proposal),
        patch("agent.graph.WRITE_NOTE_ROOT", tmp_path),
        patch("routers.chat.StreamingResponse", side_effect=lambda content, **kwargs: content),
    ):
        pause_frames = list(
            chat.chat_stream(body, db=MagicMock(), current_user=SimpleNamespace(id=7))
        )

    confirmation = json.loads(pause_frames[0].removeprefix("data: "))[
        "confirmation_required"
    ]
    assert confirmation["thread_id"] == thread_id
    assert pause_frames[-1] == "data: [PAUSED]\n\n"
    assert not list(tmp_path.rglob("*.md"))

    persisted: list[dict] = []
    app = FastAPI()
    app.include_router(chat.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=7)
    with (
        patch("agent.graph.WRITE_NOTE_ROOT", tmp_path),
        patch("routers.chat.SessionLocal", return_value=MagicMock()),
        patch(
            "routers.chat.persist_conversation_once",
            side_effect=lambda *args, **kwargs: persisted.append(kwargs),
        ),
        patch("routers.chat.llm_stream", side_effect=AssertionError("unexpected Gemini")),
    ):
        response = TestClient(app).post(
            f"/api/chat/actions/{thread_id}/resolve",
            json={"action_id": confirmation["action_id"], "decision": "approve"},
        )

    target = next(tmp_path.rglob("*.md"))
    assert target.read_text() == "# Summary\n\nDeterministic summary body.\n"
    assert "Deterministic summary body." not in response.text
    assert "The note was saved to notes/" in response.text
    assert persisted[0]["thread_id"] == thread_id
    assert persisted[0]["answer"] in response.text
    assert response.text.endswith("data: [DONE]\n\n")
