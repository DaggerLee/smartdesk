from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

import agent.graph as agent_graph
from agent.graph import resume_graph, resume_graph_action, stream_graph
from agent.tools.write_note import WriteNoteTool
from llm.client import LLMResponse, ToolCall


def test_crash_after_publication_before_checkpoint_replays_without_second_write(
    tmp_path: Path,
) -> None:
    thread_id = f"hitl-crash-{uuid.uuid4().hex}"
    proposal = LLMResponse(
        text=None,
        tool_calls=[ToolCall(name="write_note", args={"title": "Title", "content": "Body"})],
        raw={},
    )
    with (
        patch("agent.graph.route", return_value="agent"),
        patch("agent.graph.complete", return_value=proposal),
        patch("agent.graph.is_hitl_write_note_enabled", return_value=True),
        patch("agent.graph.WRITE_NOTE_ROOT", tmp_path),
    ):
        list(stream_graph("Save this as a Markdown note", 1, thread_id=thread_id, user_id=7))

    snapshot = agent_graph._compiled_graph.get_state(
        {"configurable": {"thread_id": thread_id}}
    )
    action_id = snapshot.values["pending_action"]["action_id"]
    original_run = WriteNoteTool.run

    def publish_then_crash(self, title: str, content: str):
        original_run(self, title, content)
        raise KeyboardInterrupt("crash after atomic publication")

    with (
        patch("agent.graph.WRITE_NOTE_ROOT", tmp_path),
        patch("agent.graph.WriteNoteTool.run", new=publish_then_crash),
    ):
        with pytest.raises(KeyboardInterrupt, match="atomic publication"):
            list(
                resume_graph_action(
                    thread_id,
                    {"action_id": action_id, "decision": "approve"},
                )
            )

    target = tmp_path / "users" / "7" / "notes" / f"title-{action_id}.md"
    assert target.read_text() == "# Title\n\nBody\n"
    interrupted = agent_graph._compiled_graph.get_state(
        {"configurable": {"thread_id": thread_id}}
    )
    assert interrupted.next == ("write_action_node",)

    with (
        patch("agent.graph.WRITE_NOTE_ROOT", tmp_path),
        patch("agent.graph.complete") as complete_mock,
        patch("agent.graph._check_groundedness") as groundedness_mock,
    ):
        final = resume_graph(thread_id)

    assert final["pending_action"]["status"] == "replayed"
    assert final["pending_action"]["receipt"]["result"] == "replayed"
    assert final["verification_source"] == "action_receipt"
    assert [path.name for path in target.parent.iterdir()] == [target.name]
    complete_mock.assert_not_called()
    groundedness_mock.assert_not_called()
