from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import agent.graph as graph
from agent.graph import resume_graph_action, stream_graph
from llm.client import LLMResponse, ToolCall


def _mixed_round() -> LLMResponse:
    return LLMResponse(
        text=None,
        tool_calls=[
            ToolCall(name="retrieve", args={"query": "q"}),
            ToolCall(name="write_note", args={"title": "Title", "content": "Body"}),
        ],
        raw={},
    )


def test_one_invalid_round_then_text_clears_retry_flag_and_finishes() -> None:
    responses = [
        _mixed_round(),
        LLMResponse(text="I could not save the note.", tool_calls=[], raw={}),
    ]
    grounded = {"supported": True, "unsupported_sentences": []}
    with (
        patch("agent.graph.route", return_value="agent"),
        patch("agent.graph.complete", side_effect=responses) as complete_mock,
        patch("agent.graph.is_hitl_write_note_enabled", return_value=True),
        patch("agent.graph._check_groundedness", return_value=grounded),
    ):
        events = list(
            stream_graph(
                "Save this as a Markdown note",
                kb_id=1,
                user_id=7,
                thread_id=f"clear-invalid-{uuid.uuid4().hex}",
            )
        )

    assert complete_mock.call_count == 2
    assert events[-1].data["answer"] == "I could not save the note."
    assert events[-1].data["invalid_write_round"] is False
    assert events[-1].data["verification_source"] == "llm_groundedness"


@pytest.mark.parametrize(
    "args",
    [
        {"title": "private/unsafe-title", "content": "secret body"},
        {"title": "Title", "content": "secret body", "path": "/tmp/escape"},
    ],
)
def test_invalid_write_arguments_become_bounded_redacted_protocol_errors(
    args: dict[str, str],
) -> None:
    invalid = LLMResponse(
        text=None,
        tool_calls=[ToolCall(name="write_note", args=args)],
        raw={},
    )
    traces: list[dict] = []
    with (
        patch("agent.graph.route", return_value="agent"),
        patch("agent.graph.complete", return_value=invalid) as complete_mock,
        patch("agent.graph.is_hitl_write_note_enabled", return_value=True),
        patch("agent.graph._trace_write", side_effect=traces.append),
    ):
        events = list(
            stream_graph(
                "Save this as a Markdown note",
                kb_id=1,
                user_id=7,
                thread_id=f"invalid-args-{uuid.uuid4().hex}",
            )
        )

    assert complete_mock.call_count == 2
    assert events[-1].data["verification_status"] == "rejected"
    assert events[-1].data["verification_source"] == "llm_groundedness"
    assert events[-1].data["tool_fail_counts"]["write_protocol"] == 2
    serialized = repr(traces) + repr(events[-1].data["messages"][-1])
    assert "private/unsafe-title" not in serialized
    assert "secret body" not in serialized
    assert "/tmp/escape" not in serialized


def test_missing_authenticated_user_never_exposes_write_note() -> None:
    declarations: list[list[dict]] = []

    def answer(messages, tools=None, system=None):
        declarations.append(tools or [])
        return LLMResponse(text="No write attempted.", tool_calls=[], raw={})

    with (
        patch("agent.graph.route", return_value="agent"),
        patch("agent.graph.complete", side_effect=answer),
        patch("agent.graph.is_hitl_write_note_enabled", return_value=True),
        patch(
            "agent.graph._check_groundedness",
            return_value={"supported": True, "unsupported_sentences": []},
        ),
    ):
        list(
            stream_graph(
                "Save this as a Markdown note",
                kb_id=1,
                thread_id=f"missing-user-{uuid.uuid4().hex}",
            )
        )

    assert all(item.get("name") != "write_note" for item in declarations[0])


def test_resume_does_not_emit_receipt_before_graph_is_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    action_id = "a" * 32

    class NonTerminalGraph:
        def stream(self, *args, **kwargs):
            return iter(())

        def get_state(self, config):
            return SimpleNamespace(
                next=("unexpected_node",),
                values={
                    "pending_action": {
                        "receipt": {
                            "action_id": action_id,
                            "result": "succeeded",
                            "relative_path": f"notes/title-{action_id}.md",
                            "content_hash": "0" * 64,
                            "byte_count": 12,
                            "read_back_verified": True,
                        }
                    }
                },
            )

    monkeypatch.setattr(graph, "_compiled_graph", NonTerminalGraph())

    events = resume_graph_action(
        "thread-id",
        {"action_id": action_id, "decision": "approve"},
    )
    with pytest.raises(RuntimeError, match="did not reach a terminal state"):
        next(events)
