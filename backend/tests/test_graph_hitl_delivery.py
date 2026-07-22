from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.graph import resume_graph_action, stream_graph
from llm.client import LLMResponse, ToolCall


@pytest.mark.parametrize("verified_flag", ["0", "1"])
def test_action_receipt_delivery_is_identical_and_model_free_for_both_flag_states(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verified_flag: str
) -> None:
    monkeypatch.setenv("SMARTDESK_VERIFIED_AGENT_DELIVERY", verified_flag)
    thread_id = f"receipt-flag-{verified_flag}-{uuid.uuid4().hex}"
    root = tmp_path / verified_flag
    root.mkdir()
    proposal = LLMResponse(
        text=None,
        tool_calls=[ToolCall(name="write_note", args={"title": "Title", "content": "Body"})],
        raw={},
    )
    with (
        patch("agent.graph.route", return_value="agent"),
        patch("agent.graph.complete", return_value=proposal),
        patch("agent.graph.is_hitl_write_note_enabled", return_value=True),
        patch("agent.graph.WRITE_NOTE_ROOT", root),
        patch("agent.graph.uuid.uuid4") as action_uuid,
    ):
        action_uuid.return_value.hex = "stable-action"
        list(stream_graph("Save this as a Markdown note", 1, thread_id=thread_id, user_id=7))

    with (
        patch("agent.graph.WRITE_NOTE_ROOT", root),
        patch("agent.graph.complete") as complete_mock,
        patch("agent.graph.llm_stream") as stream_mock,
    ):
        events = list(
            resume_graph_action(
                thread_id,
                {"action_id": "stable-action", "decision": "approve"},
            )
        )

    expected = "The note was saved to notes/title-stable-action.md."
    assert events[-1].data["answer"] == expected
    assert events[-1].data["verification_source"] == "action_receipt"
    complete_mock.assert_not_called()
    stream_mock.assert_not_called()
