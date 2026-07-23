from __future__ import annotations

import uuid
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.graph import resume_graph_action, stream_graph
from agent.write_action import ActionReceipt
from llm.client import LLMResponse, ToolCall


def _response(*calls: ToolCall) -> LLMResponse:
    return LLMResponse(text=None, tool_calls=list(calls), raw={})


@pytest.mark.parametrize(
    "calls",
    [
        (
            ToolCall(name="retrieve", args={"query": "q"}),
            ToolCall(name="write_note", args={"title": "Title", "content": "Body"}),
        ),
        (
            ToolCall(name="write_note", args={"title": "One", "content": "Body"}),
            ToolCall(name="write_note", args={"title": "Two", "content": "Body"}),
        ),
    ],
)
def test_invalid_write_round_executes_no_tools_and_stops_at_shared_failure_cap(
    tmp_path: Path, calls: tuple[ToolCall, ...]
) -> None:
    with (
        patch("agent.graph.route", return_value="agent"),
        patch("agent.graph.complete", return_value=_response(*calls)) as complete_mock,
        patch("agent.graph.is_hitl_write_note_enabled", return_value=True),
        patch("agent.graph.WRITE_NOTE_ROOT", tmp_path),
        patch("agent.tools.retrieve.RetrieveTool.run") as retrieve_mock,
        patch("agent.graph.WriteNoteTool.run") as write_mock,
        patch("agent.graph._check_groundedness") as groundedness_mock,
    ):
        events = list(
            stream_graph(
                "Save this as a Markdown note",
                kb_id=1,
                user_id=7,
                thread_id=f"invalid-round-{uuid.uuid4().hex}",
            )
        )

    assert [event.type for event in events] == ["final"]
    assert complete_mock.call_count == 2
    assert events[0].data["tool_fail_counts"]["write_protocol"] == 2
    assert events[0].data["verification_status"] == "rejected"
    retrieve_mock.assert_not_called()
    write_mock.assert_not_called()
    groundedness_mock.assert_not_called()
    assert list(tmp_path.iterdir()) == []


def _propose(thread_id: str, tmp_path: Path) -> str:
    response = _response(
        ToolCall(name="write_note", args={"title": "Title", "content": "Body"})
    )
    with (
        patch("agent.graph.route", return_value="agent"),
        patch("agent.graph.complete", return_value=response),
        patch("agent.graph.is_hitl_write_note_enabled", return_value=True),
        patch("agent.graph.WRITE_NOTE_ROOT", tmp_path),
    ):
        list(stream_graph("Save this as a Markdown note", 1, thread_id=thread_id, user_id=7))
    import agent.graph as graph

    return graph._compiled_graph.get_state(
        {"configurable": {"thread_id": thread_id}}
    ).values["pending_action"]["action_id"]


@pytest.mark.parametrize("result", ["replayed", "conflict", "failed"])
def test_terminal_receipts_use_legal_response_and_receipt_only_finalization(
    tmp_path: Path, result: str
) -> None:
    thread_id = f"receipt-{result}-{uuid.uuid4().hex}"
    action_id = _propose(thread_id, tmp_path)
    if result in {"replayed", "conflict"}:
        notes = tmp_path / "users" / "7" / "notes"
        notes.mkdir(parents=True)
        content = "# Title\n\nBody\n" if result == "replayed" else "different"
        (notes / f"title-{action_id}.md").write_text(content)

    failed = ActionReceipt(action_id=action_id, result="failed", error_code="write_failed")
    trace_events: list[dict] = []
    with ExitStack() as stack:
        stack.enter_context(patch("agent.graph.WRITE_NOTE_ROOT", tmp_path))
        complete_mock = stack.enter_context(patch("agent.graph.complete"))
        groundedness_mock = stack.enter_context(patch("agent.graph._check_groundedness"))
        stack.enter_context(patch("agent.graph._trace_write", side_effect=trace_events.append))
        if result == "failed":
            stack.enter_context(patch("agent.graph.WriteNoteTool.run", return_value=failed))
        events = list(
            resume_graph_action(thread_id, {"action_id": action_id, "decision": "approve"})
        )

    assert events[0].data["result"] == result
    final = events[-1].data
    assert final["verification_source"] == "action_receipt"
    response = final["messages"][-1]["parts"][0]["functionResponse"]
    assert response["name"] == "write_note"
    assert response["response"]["result"] == result
    complete_mock.assert_not_called()
    groundedness_mock.assert_not_called()
    assert trace_events
    for event in trace_events:
        for forbidden in ("title", "content", "reason", "original_payload", "approved_payload"):
            assert forbidden not in event
