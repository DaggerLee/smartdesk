from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import get_current_user
from routers import chat


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(chat.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=7)
    return TestClient(app)


def _terminal_pending(decision: str) -> dict:
    original = {"title": "Original", "content": "Original body"}
    if decision == "edit":
        approved = {"title": "Edited", "content": "Edited body"}
        reason = None
        result = "succeeded"
        status = "succeeded"
    else:
        approved = None
        reason = "Not needed"
        result = "rejected"
        status = "rejected"
    return {
        "action_id": "action-1",
        "user_id": 7,
        "tool": "write_note",
        "original_payload": original,
        "approved_payload": approved,
        "decision": decision,
        "reject_reason": reason,
        "status": status,
        "receipt": {
            "action_id": "action-1",
            "tool": "write_note",
            "result": result,
            "relative_path": "notes/edited-action-1.md" if decision == "edit" else None,
            "content_hash": "0" * 64 if decision == "edit" else None,
            "byte_count": 12 if decision == "edit" else None,
            "read_back_verified": decision == "edit",
            "error_code": None,
        },
        "error": None,
    }


@pytest.mark.parametrize(
    ("decision", "exact", "conflict"),
    [
        (
            "edit",
            {
                "action_id": "action-1",
                "decision": "edit",
                "title": "Edited",
                "content": "Edited body",
            },
            {
                "action_id": "action-1",
                "decision": "edit",
                "title": "Edited",
                "content": "Different body",
            },
        ),
        (
            "reject",
            {"action_id": "action-1", "decision": "reject", "reason": "Not needed"},
            {"action_id": "action-1", "decision": "reject", "reason": "Changed"},
        ),
    ],
)
def test_terminal_edit_and_reject_require_exact_resolution_identity(
    decision: str,
    exact: dict,
    conflict: dict,
) -> None:
    pending = _terminal_pending(decision)
    snapshot = {
        "query": "Save this as a note",
        "kb_id": 3,
        "pending_action": pending,
        "answer": "Canonical receipt answer.",
        "verification_status": "verified",
        "verification_source": "action_receipt",
    }
    with (
        patch("routers.chat.get_graph_snapshot", return_value=snapshot),
        patch("routers.chat.resume_graph_action") as resume,
        patch("routers.chat.SessionLocal", return_value=MagicMock()),
        patch("routers.chat.persist_conversation_once"),
    ):
        repeated = _client().post("/api/chat/actions/thread-1/resolve", json=exact)
        changed = _client().post("/api/chat/actions/thread-1/resolve", json=conflict)

    assert repeated.status_code == 200
    assert changed.status_code == 409
    resume.assert_not_called()
