from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import patch

import agent.graph as agent_graph
from agent.graph import resume_graph_action, stream_graph
from llm.client import LLMResponse, ToolCall


def _write_call(title: str = "Project Notes", content: str = "Body") -> LLMResponse:
    return LLMResponse(
        text=None,
        tool_calls=[ToolCall(name="write_note", args={"title": title, "content": content})],
        raw={},
    )


def _pending(thread_id: str) -> dict:
    snapshot = agent_graph._compiled_graph.get_state(
        {"configurable": {"thread_id": thread_id}}
    )
    return snapshot.values["pending_action"]


def _propose(thread_id: str, tmp_path: Path, response: LLMResponse) -> None:
    with (
        patch("agent.graph.route", return_value="agent"),
        patch("agent.graph.complete", return_value=response),
        patch("agent.graph.is_hitl_write_note_enabled", return_value=True),
        patch("agent.graph.WRITE_NOTE_ROOT", tmp_path),
    ):
        events = list(
            stream_graph(
                "Save this as a Markdown note",
                kb_id=1,
                user_id=7,
                thread_id=thread_id,
            )
        )
    assert [event.type for event in events] == ["confirmation_required"]


def test_approve_resumes_executes_once_and_finalizes_from_receipt(tmp_path: Path) -> None:
    thread_id = f"hitl-approve-{uuid.uuid4().hex}"
    _propose(thread_id, tmp_path, _write_call())

    with (
        patch("agent.graph.WRITE_NOTE_ROOT", tmp_path),
        patch("agent.graph.complete") as complete_mock,
        patch("agent.graph._check_groundedness") as groundedness_mock,
    ):
        events = list(
            resume_graph_action(
                thread_id,
                {"action_id": _pending(thread_id)["action_id"], "decision": "approve"},
            )
        )

    assert [event.type for event in events] == ["action_result", "final"]
    assert events[0].data["result"] == "succeeded"
    final = events[1].data
    assert final["verification_status"] == "verified"
    assert final["verification_source"] == "action_receipt"
    assert "saved" in final["answer"]
    assert final["messages"][-1]["parts"][0]["functionResponse"]["name"] == "write_note"
    complete_mock.assert_not_called()
    groundedness_mock.assert_not_called()


def test_edit_preserves_original_and_writes_distinct_approved_payload(tmp_path: Path) -> None:
    thread_id = f"hitl-edit-{uuid.uuid4().hex}"
    _propose(thread_id, tmp_path, _write_call("Original", "Original body"))
    action_id = _pending(thread_id)["action_id"]

    with patch("agent.graph.WRITE_NOTE_ROOT", tmp_path), patch("agent.graph.complete"):
        list(
            resume_graph_action(
                thread_id,
                {
                    "action_id": action_id,
                    "decision": "edit",
                    "title": "Edited",
                    "content": "Edited body",
                },
            )
        )

    pending = _pending(thread_id)
    assert pending["original_payload"] == {"title": "Original", "content": "Original body"}
    assert pending["approved_payload"] == {"title": "Edited", "content": "Edited body"}
    target = tmp_path / "users" / "7" / "notes" / f"edited-{action_id}.md"
    assert target.read_text() == "# Edited\n\nEdited body\n"


def test_reject_never_invokes_writer_or_creates_directories(tmp_path: Path) -> None:
    thread_id = f"hitl-reject-{uuid.uuid4().hex}"
    _propose(thread_id, tmp_path, _write_call())
    action_id = _pending(thread_id)["action_id"]

    with (
        patch("agent.graph.WRITE_NOTE_ROOT", tmp_path),
        patch("agent.graph.WriteNoteTool.run") as writer_mock,
        patch("agent.graph.complete") as complete_mock,
        patch("agent.graph._check_groundedness") as groundedness_mock,
    ):
        events = list(
            resume_graph_action(
                thread_id,
                {"action_id": action_id, "decision": "reject", "reason": "Not needed"},
            )
        )

    assert events[0].data["result"] == "rejected"
    assert events[-1].data["verification_source"] == "action_receipt"
    assert "rejected" in events[-1].data["answer"]
    assert list(tmp_path.iterdir()) == []
    writer_mock.assert_not_called()
    complete_mock.assert_not_called()
    groundedness_mock.assert_not_called()
