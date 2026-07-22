from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.graph import GraphEvent
from auth import get_current_user
from routers import chat


def _app(user_id: int = 7) -> TestClient:
    app = FastAPI()
    app.include_router(chat.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)
    return TestClient(app)


def _pending(*, user_id: int = 7, terminal: bool = False) -> dict:
    action_id = "action-1"
    value = {
        "action_id": action_id,
        "user_id": user_id,
        "tool": "write_note",
        "original_payload": {"title": "Title", "content": "Body"},
        "approved_payload": None,
        "decision": None,
        "reject_reason": None,
        "status": "proposed",
        "receipt": None,
        "error": None,
    }
    if terminal:
        value.update(
            {
                "approved_payload": {"title": "Title", "content": "Body"},
                "decision": "approve",
                "status": "succeeded",
                "receipt": {
                    "action_id": action_id,
                    "tool": "write_note",
                    "result": "succeeded",
                    "relative_path": "notes/title-action-1.md",
                    "content_hash": "0" * 64,
                    "byte_count": 14,
                    "read_back_verified": True,
                    "error_code": None,
                },
            }
        )
    return value


def _snapshot(pending: dict, answer: str = "The note was saved.") -> dict:
    return {
        "query": "Save this as a note",
        "kb_id": 3,
        "pending_action": pending,
        "answer": answer,
        "verification_status": "verified",
        "verification_source": "action_receipt",
    }


def test_resolve_request_is_strict_and_rejects_extra_fields() -> None:
    response = _app().post(
        "/api/chat/actions/thread-1/resolve",
        json={"action_id": "action-1", "decision": "approve", "user_id": 7},
    )

    assert response.status_code == 422


@pytest.mark.parametrize("pending", [None, _pending(user_id=8)])
def test_unknown_or_cross_user_action_returns_404(pending) -> None:
    with patch("routers.chat.get_graph_snapshot", return_value=_snapshot(pending) if pending else None):
        response = _app().post(
            "/api/chat/actions/thread-1/resolve",
            json={"action_id": "action-1", "decision": "approve"},
        )

    assert response.status_code == 404


def test_owner_approve_resumes_commits_then_returns_ordered_sse() -> None:
    proposed = _snapshot(_pending())
    final = _snapshot(_pending(terminal=True))
    resume_events = [
        GraphEvent(type="action_result", data=final["pending_action"]["receipt"]),
        GraphEvent(type="final", data=final),
    ]
    events: list[str] = []
    db = MagicMock()

    def persist(*args, **kwargs):
        events.append("conversation_commit")

    with (
        patch("routers.chat.get_graph_snapshot", side_effect=[proposed, final]),
        patch("routers.chat.resume_graph_action", return_value=iter(resume_events)) as resume,
        patch("routers.chat.SessionLocal", return_value=db),
        patch("routers.chat.persist_conversation_once", side_effect=persist),
    ):
        response = _app().post(
            "/api/chat/actions/thread-1/resolve",
            json={"action_id": "action-1", "decision": "approve"},
        )

    assert response.status_code == 200
    assert "conversation_commit" in events
    assert response.text.index('"action_result"') < response.text.index("The note was saved.")
    assert response.text.endswith("data: [DONE]\n\n")
    resume.assert_called_once()
    db.close.assert_called_once()


def test_exact_terminal_resolution_reuses_receipt_without_resuming() -> None:
    final = _snapshot(_pending(terminal=True))
    with (
        patch("routers.chat.get_graph_snapshot", return_value=final),
        patch("routers.chat.resume_graph_action") as resume,
        patch("routers.chat.SessionLocal", return_value=MagicMock()),
        patch("routers.chat.persist_conversation_once"),
    ):
        response = _app().post(
            "/api/chat/actions/thread-1/resolve",
            json={"action_id": "action-1", "decision": "approve"},
        )

    assert response.status_code == 200
    assert '"result": "succeeded"' in response.text
    resume.assert_not_called()


@pytest.mark.parametrize(
    "resolution",
    [
        {"action_id": "action-1", "decision": "reject", "reason": "Changed"},
        {
            "action_id": "action-1",
            "decision": "edit",
            "title": "Edited",
            "content": "Edited body",
        },
    ],
)
def test_terminal_action_rejects_conflicting_resolution(resolution) -> None:
    with patch("routers.chat.get_graph_snapshot", return_value=_snapshot(_pending(terminal=True))):
        response = _app().post("/api/chat/actions/thread-1/resolve", json=resolution)

    assert response.status_code == 409


def test_initial_write_request_emits_confirmation_then_paused_and_passes_user_id() -> None:
    body = chat.ChatRequest(kb_id=3, message="Save this as a note")
    confirmation = GraphEvent(
        type="confirmation_required",
        data={"action_id": "action-1", "tool": "write_note", "title": "Title", "content": "Body"},
    )
    with (
        patch.dict(
            "os.environ",
            {"SMARTDESK_AGENT_BACKEND": "langgraph", "SMARTDESK_HITL_WRITE_NOTE": "true"},
            clear=False,
        ),
        patch("routers.chat._owned_kb"),
        patch("routers.chat.uuid.uuid4", return_value=SimpleNamespace(hex="thread-1")),
        patch("routers.chat._recent_usable_history", return_value=[]),
        patch("routers.chat.stream_graph", return_value=iter([confirmation])) as stream,
        patch("routers.chat.StreamingResponse", side_effect=lambda content, **kwargs: content),
    ):
        frames = list(
            chat.chat_stream(body, db=MagicMock(), current_user=SimpleNamespace(id=7))
        )

    assert '"confirmation_required"' in frames[0]
    assert '"thread_id": "thread-1"' in frames[0]
    assert frames[-1] == "data: [PAUSED]\n\n"
    stream.assert_called_once()
    assert stream.call_args.kwargs["user_id"] == 7


def test_proposal_failure_emits_typed_error_then_failed() -> None:
    body = chat.ChatRequest(kb_id=3, message="Save this as a note")
    with (
        patch.dict(
            "os.environ",
            {"SMARTDESK_AGENT_BACKEND": "langgraph", "SMARTDESK_HITL_WRITE_NOTE": "true"},
            clear=False,
        ),
        patch("routers.chat._owned_kb"),
        patch("routers.chat._recent_usable_history", return_value=[]),
        patch("routers.chat.stream_graph", side_effect=RuntimeError("checkpoint failed")),
        patch("routers.chat.StreamingResponse", side_effect=lambda content, **kwargs: content),
    ):
        frames = list(
            chat.chat_stream(body, db=MagicMock(), current_user=SimpleNamespace(id=7))
        )

    assert '"stage": "proposal"' in frames[0]
    assert frames[-1] == "data: [FAILED]\n\n"
    assert all("[DONE]" not in frame for frame in frames)


def test_action_checkpoint_failure_emits_no_action_result() -> None:
    proposed = _snapshot(_pending())
    resume_events = [
        GraphEvent(
            type="action_result",
            data={"action_id": "action-1", "result": "succeeded"},
        )
    ]
    with (
        patch("routers.chat.get_graph_snapshot", side_effect=[proposed, None]),
        patch("routers.chat.resume_graph_action", return_value=iter(resume_events)),
    ):
        response = _app().post(
            "/api/chat/actions/thread-1/resolve",
            json={"action_id": "action-1", "decision": "approve"},
        )

    assert response.status_code == 200
    assert '"action_result":' not in response.text
    assert '"stage": "action_result"' in response.text
    assert response.text.endswith("data: [FAILED]\n\n")


def test_conversation_failure_keeps_committed_action_result_but_no_final_answer() -> None:
    proposed = _snapshot(_pending())
    final = _snapshot(_pending(terminal=True))
    resume_events = [
        GraphEvent(type="action_result", data=final["pending_action"]["receipt"]),
        GraphEvent(type="final", data=final),
    ]
    db = MagicMock()
    with (
        patch("routers.chat.get_graph_snapshot", side_effect=[proposed, final]),
        patch("routers.chat.resume_graph_action", return_value=iter(resume_events)),
        patch("routers.chat.SessionLocal", return_value=db),
        patch(
            "routers.chat.persist_conversation_once",
            side_effect=RuntimeError("database unavailable"),
        ),
    ):
        response = _app().post(
            "/api/chat/actions/thread-1/resolve",
            json={"action_id": "action-1", "decision": "approve"},
        )

    assert '"action_result"' in response.text
    assert '"stage": "conversation"' in response.text
    assert "The note was saved." not in response.text
    assert "[DONE]" not in response.text
    assert response.text.endswith("data: [FAILED]\n\n")


@pytest.mark.parametrize("verified_flag", ["0", "1"])
def test_receipt_answer_is_flag_independent_and_matches_persisted_answer(
    verified_flag: str,
) -> None:
    final = _snapshot(_pending(terminal=True), answer="Canonical receipt answer.")
    persisted: list[str] = []
    db = MagicMock()
    with (
        patch.dict(
            "os.environ",
            {"SMARTDESK_VERIFIED_AGENT_DELIVERY": verified_flag},
            clear=False,
        ),
        patch("routers.chat.get_graph_snapshot", return_value=final),
        patch("routers.chat.SessionLocal", return_value=db),
        patch(
            "routers.chat.persist_conversation_once",
            side_effect=lambda *args, **kwargs: persisted.append(kwargs["answer"]),
        ),
        patch("routers.chat.llm_stream", side_effect=AssertionError("unexpected Gemini")),
    ):
        response = _app().post(
            "/api/chat/actions/thread-1/resolve",
            json={"action_id": "action-1", "decision": "approve"},
        )

    assert persisted == ["Canonical receipt answer."]
    assert 'data: "Canonical receipt answer."' in response.text
    assert response.text.endswith("data: [DONE]\n\n")


def test_non_write_graph_failure_is_not_mislabeled_as_proposal_failure() -> None:
    with (
        patch("routers.chat.stream_graph", side_effect=RuntimeError("ordinary failure")),
        patch("routers.chat.is_hitl_write_note_enabled", return_value=True),
    ):
        with pytest.raises(RuntimeError, match="ordinary failure"):
            list(chat._stream_graph_with_proposal_failure("hello", 3))


def test_conversation_identity_conflict_returns_409() -> None:
    final = _snapshot(_pending(terminal=True))
    with (
        patch("routers.chat.get_graph_snapshot", return_value=final),
        patch("routers.chat.SessionLocal", return_value=MagicMock()),
        patch(
            "routers.chat.persist_conversation_once",
            side_effect=chat.ConversationThreadConflictError("conflict"),
        ),
    ):
        response = _app().post(
            "/api/chat/actions/thread-1/resolve",
            json={"action_id": "action-1", "decision": "approve"},
        )

    assert response.status_code == 409
