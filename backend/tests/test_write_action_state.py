from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent.write_action import (
    ActionReceipt,
    PendingAction,
    WriteNotePayload,
    render_action_answer,
    validate_write_note_payload,
)


def test_validate_write_note_payload_is_the_single_validation_entry_point() -> None:
    payload = validate_write_note_payload("Title", "  body\n")
    assert payload == WriteNotePayload(title="Title", content="  body\n")


def test_pending_action_preserves_distinct_original_and_approved_payloads() -> None:
    original = WriteNotePayload(title="Original", content="Original body")
    approved = WriteNotePayload(title="Edited", content="Edited body")
    action = PendingAction(
        action_id="act-1",
        user_id=7,
        original_payload=original,
        approved_payload=approved,
        decision="edit",
        status="approved",
    )
    assert action.original_payload == original
    assert action.approved_payload == approved
    assert action.original_payload is not action.approved_payload
    with pytest.raises(ValidationError):
        action.original_payload = approved


def test_pending_action_reject_keeps_original_and_has_no_approved_payload() -> None:
    action = PendingAction(
        action_id="act-1",
        user_id=7,
        original_payload=WriteNotePayload(title="Original", content="Original body"),
        decision="reject",
        reject_reason="Not needed",
        status="rejected",
    )
    assert action.approved_payload is None
    assert action.original_payload.title == "Original"


@pytest.mark.parametrize(
    ("result", "english", "chinese"),
    [
        ("succeeded", "saved", "已保存"),
        ("replayed", "already saved", "已存在"),
        ("rejected", "rejected", "已拒绝"),
        ("conflict", "conflicts", "冲突"),
        ("failed", "could not be saved", "保存失败"),
    ],
)
def test_receipt_templates_support_deterministic_english_and_chinese(
    result: str, english: str, chinese: str
) -> None:
    receipt = ActionReceipt(
        action_id="act-1",
        result=result,
        relative_path="notes/note-act-1.md" if result in {"succeeded", "replayed"} else None,
        content_hash="deadbeef" if result in {"succeeded", "replayed"} else None,
        byte_count=42 if result in {"succeeded", "replayed"} else None,
        read_back_verified=result in {"succeeded", "replayed"},
        error_code="write_failed" if result == "failed" else None,
    )
    assert english in render_action_answer(receipt, language="en")
    assert chinese in render_action_answer(receipt, language="zh")
