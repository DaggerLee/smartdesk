from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import patch

import agent.graph as agent_graph
from agent.graph import stream_graph
from llm.client import LLMResponse, ToolCall


def _write_call(title: str = "Project Notes", content: str = "Body") -> LLMResponse:
    return LLMResponse(
        text=None,
        tool_calls=[ToolCall(name="write_note", args={"title": title, "content": content})],
        raw={},
    )


def test_write_proposal_is_checkpointed_before_interrupt_without_filesystem_effects(
    tmp_path: Path,
) -> None:
    thread_id = f"hitl-proposal-{uuid.uuid4().hex}"
    captured_tools: list[list[dict]] = []

    def complete_with_write(messages, tools=None, system=None):
        captured_tools.append(tools or [])
        return _write_call()

    with (
        patch("agent.graph.route", return_value="agent"),
        patch("agent.graph.complete", side_effect=complete_with_write),
        patch("agent.graph.is_hitl_write_note_enabled", return_value=True),
        patch("agent.graph.WRITE_NOTE_ROOT", tmp_path),
        patch("agent.graph.uuid.uuid4") as action_uuid,
    ):
        action_uuid.return_value.hex = "stable-action-id"
        events = list(
            stream_graph(
                "Save this as a Markdown note",
                kb_id=1,
                user_id=7,
                thread_id=thread_id,
            )
        )

    assert any(
        declaration.get("name") == "write_note"
        for declaration in captured_tools[0]
    )
    assert [event.type for event in events] == ["confirmation_required"]
    assert events[0].data["action_id"] == "stable-action-id"
    assert events[0].data["title"] == "Project Notes"
    assert list(tmp_path.iterdir()) == []

    snapshot = agent_graph._compiled_graph.get_state(
        {"configurable": {"thread_id": thread_id}}
    )
    pending = snapshot.values["pending_action"]
    assert pending["action_id"] == "stable-action-id"
    assert pending["user_id"] == 7
    assert pending["status"] == "proposed"
    assert pending["original_payload"] == {
        "title": "Project Notes",
        "content": "Body",
    }
    assert snapshot.next == ("approval_gate",)
