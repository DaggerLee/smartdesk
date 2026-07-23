from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from agent.write_action import (
    ACTION_STATUSES,
    ActionReceipt,
    ActionResolution,
    WriteNotePayload,
    render_action_answer,
    to_action_evidence,
)


def test_action_statuses_are_the_frozen_terminal_vocabulary() -> None:
    assert ACTION_STATUSES == (
        "proposed",
        "approved",
        "rejected",
        "succeeded",
        "replayed",
        "conflict",
        "failed",
    )


@pytest.mark.parametrize(
    "data",
    [
        {"action_id": "act-1", "decision": "approve"},
        {
            "action_id": "act-1",
            "decision": "edit",
            "title": "Edited title",
            "content": "Edited content",
        },
        {"action_id": "act-1", "decision": "reject"},
        {"action_id": "act-1", "decision": "reject", "reason": "Not needed"},
    ],
)
def test_resolution_accepts_only_valid_three_state_shapes(data: dict[str, str]) -> None:
    resolution = TypeAdapter(ActionResolution).validate_python(data)
    assert resolution.action_id == "act-1"


@pytest.mark.parametrize(
    "data",
    [
        {"action_id": "act-1", "decision": "approve", "title": "extra"},
        {"action_id": "act-1", "decision": "approve", "user_id": 7},
        {"action_id": "act-1", "decision": "edit", "title": "Missing content"},
        {"action_id": "act-1", "decision": "reject", "content": "extra"},
        {"action_id": "act-1", "decision": "maybe"},
    ],
)
def test_resolution_rejects_extra_fields_and_invalid_shapes(data: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(ActionResolution).validate_python(data)


@pytest.mark.parametrize("title", ["", " ", ".", "..", "a/b", "a\\b", "a\x00b", "a\n b"])
def test_write_payload_rejects_unsafe_titles(title: str) -> None:
    with pytest.raises(ValidationError):
        WriteNotePayload(title=title, content="content")


def test_write_payload_enforces_exact_title_and_content_boundaries() -> None:
    assert WriteNotePayload(title="t" * 120, content="c" * 50_000)
    for title in (" padded", "padded ", "t" * 121):
        with pytest.raises(ValidationError):
            WriteNotePayload(title=title, content="content")
    for content in ("", "   ", "c" * 50_001, "a\x00b"):
        with pytest.raises(ValidationError):
            WriteNotePayload(title="title", content=content)


def test_write_payload_preserves_content_whitespace_and_is_immutable() -> None:
    payload = WriteNotePayload(title="title", content="  body\n")
    assert payload.content == "  body\n"
    with pytest.raises(ValidationError):
        payload.content = "changed"


@pytest.mark.parametrize("reason", ["", " reason", "reason ", "r" * 501, "a\x00b"])
def test_reject_reason_has_fixed_optional_rules(reason: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(ActionResolution).validate_python(
            {"action_id": "act-1", "decision": "reject", "reason": reason}
        )


@pytest.mark.parametrize("result", ["succeeded", "replayed", "rejected", "conflict", "failed"])
def test_receipt_templates_are_deterministic_and_hide_internal_metadata(result: str) -> None:
    receipt = ActionReceipt(
        action_id="act-1",
        result=result,
        relative_path="notes/note-act-1.md" if result in {"succeeded", "replayed"} else None,
        content_hash="deadbeef" if result in {"succeeded", "replayed"} else None,
        byte_count=42 if result in {"succeeded", "replayed"} else None,
        read_back_verified=result in {"succeeded", "replayed"},
        error_code="write_failed" if result == "failed" else None,
    )
    answer = render_action_answer(receipt)
    assert answer
    assert "deadbeef" not in answer
    assert "42" not in answer
    assert "write_failed" not in answer
    if result in {"succeeded", "replayed"}:
        assert "notes/note-act-1.md" in answer


def test_action_evidence_is_a_strict_receipt_metadata_whitelist() -> None:
    receipt = ActionReceipt(
        action_id="act-1",
        result="succeeded",
        relative_path="notes/note-act-1.md",
        content_hash="deadbeef",
        byte_count=42,
        read_back_verified=True,
    )
    evidence = to_action_evidence(receipt)
    assert evidence == {
        "type": "action_receipt",
        "action_id": "act-1",
        "tool": "write_note",
        "result": "succeeded",
        "relative_path": "notes/note-act-1.md",
        "content_hash": "deadbeef",
        "byte_count": 42,
        "read_back_verified": True,
        "error_code": None,
    }
    for forbidden in ("title", "content", "reason", "original_payload", "approved_payload"):
        assert forbidden not in evidence
