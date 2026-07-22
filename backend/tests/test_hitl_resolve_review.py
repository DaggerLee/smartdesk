from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.graph import GraphEvent
from auth import get_current_user
from routers import chat


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(chat.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=7)
    return TestClient(app)


def _snapshot(*, result: str = "conflict", verified: bool = True) -> dict:
    receipt = {
        "action_id": "action-1",
        "tool": "write_note",
        "result": result,
        "relative_path": None,
        "content_hash": None,
        "byte_count": None,
        "read_back_verified": False,
        "error_code": "content_conflict" if result == "conflict" else None,
    }
    return {
        "query": "Save this as a note",
        "kb_id": 3,
        "pending_action": {
            "action_id": "action-1",
            "user_id": 7,
            "tool": "write_note",
            "original_payload": {"title": "Title", "content": "Body"},
            "approved_payload": {"title": "Title", "content": "Body"},
            "decision": "approve",
            "reject_reason": None,
            "status": result,
            "receipt": receipt,
            "error": None,
        },
        "answer": "The note could not be saved because of a conflict." if verified else "",
        "verification_status": "verified" if verified else "pending",
        "verification_source": "action_receipt" if verified else None,
    }


def test_committed_file_conflict_returns_409() -> None:
    with patch("routers.chat.get_graph_snapshot", return_value=_snapshot()):
        response = _client().post(
            "/api/chat/actions/thread-1/resolve",
            json={"action_id": "action-1", "decision": "approve"},
        )

    assert response.status_code == 409


def test_graph_finalization_failure_has_action_result_error_and_no_final_answer() -> None:
    proposed = _snapshot(verified=False)
    proposed["pending_action"]["receipt"] = None
    proposed["pending_action"]["status"] = "proposed"
    committed = _snapshot(result="succeeded", verified=False)
    with (
        patch("routers.chat.get_graph_snapshot", side_effect=[proposed, committed]),
        patch(
            "routers.chat.resume_graph_action",
            return_value=iter([GraphEvent(type="final", data=committed)]),
        ),
    ):
        response = _client().post(
            "/api/chat/actions/thread-1/resolve",
            json={"action_id": "action-1", "decision": "approve"},
        )

    assert response.status_code == 200
    assert '"action_result"' in response.text
    assert '"stage": "action_result"' in response.text
    assert "[DONE]" not in response.text
    assert response.text.endswith("data: [FAILED]\n\n")
