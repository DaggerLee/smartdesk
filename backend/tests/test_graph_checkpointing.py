"""tests/test_graph_checkpointing.py — checkpointer wiring tests (W5 T4).

Covers:
  - thread_id: auto-generated (uuid4) when omitted, honored verbatim when
    passed explicitly — see agent/graph.py module docstring for why it's
    scoped one-per-turn rather than reused across turns.
  - resume_graph(): a crash mid-run (simulated via an injected exception —
    the same effect a killed process has, since LangGraph never gets a
    chance to commit the in-flight superstep either way) does NOT cause
    resume to replay already-committed supersteps. classify_node (which ran
    before the crash) and tool_node (whose output was already committed
    before the crash happened one node later) must not re-run; only the
    interrupted node's superstep does.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

import agent.graph as agent_graph
from agent.graph import resume_graph, stream_graph
from llm.client import LLMResponse, ToolCall


def _text(t: str) -> LLMResponse:
    return LLMResponse(text=t, tool_calls=[], raw={})


def _tool(name: str, **args) -> LLMResponse:
    return LLMResponse(text=None, tool_calls=[ToolCall(name=name, args=args)], raw={})


def _retrieve_result(relevance_ok: bool = True) -> dict:
    return {
        "chunks": ["chunk text"],
        "evidence": [{"text": "chunk text", "source": "doc.pdf"}],
        "relevance_ok": relevance_ok,
    }


def _grounded_ok() -> dict:
    return {"supported": True, "unsupported_sentences": []}


# ── thread_id: auto-generate vs honor explicit value ──────────────────────────

def test_thread_id_auto_generated_when_omitted():
    with patch("agent.graph.route", return_value="direct"), \
         patch("agent.graph.llm_stream", return_value=iter(["hi"])), \
         patch("agent.graph.uuid.uuid4") as mock_uuid4:
        mock_uuid4.return_value.hex = "generated-id"
        list(stream_graph("a", kb_id=1))

    mock_uuid4.assert_called_once()


def test_thread_id_explicit_value_is_honored_not_regenerated():
    with patch("agent.graph.route", return_value="direct"), \
         patch("agent.graph.llm_stream", return_value=iter(["hi"])), \
         patch("agent.graph.uuid.uuid4") as mock_uuid4:
        list(stream_graph("a", kb_id=1, thread_id="explicit-thread-1"))

    mock_uuid4.assert_not_called()


# ── resume_graph(): crash after tool_node must not replay classify/tool_node ──

def test_resume_continues_from_last_checkpoint_not_from_scratch():
    # A fresh id per run — reusing a fixed literal would pile up unrelated
    # checkpoint history across separate pytest invocations in the shared
    # scratch sqlite file (see conftest.py's CHECKPOINT_DB_PATH override).
    thread_id = f"w5t4-resume-test-{uuid.uuid4().hex[:8]}"
    classify_calls: list[str] = []
    llm_call_count = [0]

    def _route(q: str) -> str:
        classify_calls.append(q)
        return "agent"

    def _complete_phase1(messages, tools=None, system=None):
        llm_call_count[0] += 1
        if llm_call_count[0] == 1:
            return _tool("retrieve", query="q")
        # Simulate the process dying right after tool_node's checkpoint
        # committed, before llm_node's second call can complete (and before
        # *its* superstep commits) — functionally identical to a kill -9 at
        # this point: nothing past tool_node's own output is ever persisted.
        raise RuntimeError("simulated crash right after tool_node")

    retrieve_mock = MagicMock(return_value=_retrieve_result(relevance_ok=True))

    with patch("agent.graph.route", side_effect=_route), \
         patch("agent.graph.complete", side_effect=_complete_phase1), \
         patch("agent.tools.retrieve.RetrieveTool.run", retrieve_mock):
        with pytest.raises(RuntimeError, match="simulated crash"):
            list(stream_graph("q", kb_id=1, thread_id=thread_id))

    assert classify_calls == ["q"]         # classify_node ran exactly once
    assert llm_call_count[0] == 2          # retrieve-triggering call + the crashing call
    assert retrieve_mock.call_count == 1   # tool_node ran exactly once (pre-crash)

    # Positive evidence of *where* the run stopped: the next pending task is
    # llm_node (tool_node's superstep committed; llm_node's did not) — not
    # classify, which would indicate a from-scratch restart instead of a
    # resume.
    state = agent_graph._compiled_graph.get_state({"configurable": {"thread_id": thread_id}})
    assert state.next == ("llm_node",)

    def _complete_phase2(messages, tools=None, system=None):
        llm_call_count[0] += 1
        return _text("Final answer after resume.")

    with patch("agent.graph.route", side_effect=_route), \
         patch("agent.graph.complete", side_effect=_complete_phase2), \
         patch("agent.graph._check_groundedness", return_value=_grounded_ok()), \
         patch("agent.tools.retrieve.RetrieveTool.run", retrieve_mock):
        final_state = resume_graph(thread_id)

    assert classify_calls == ["q"]         # still exactly once — no re-run on resume
    assert retrieve_mock.call_count == 1   # tool_node did NOT re-run on resume
    assert llm_call_count[0] == 3          # phase1's 2 calls + resume's 1 call
    assert final_state["answer"] == "Final answer after resume."
    assert final_state["grounded"] is True


# ── resume_graph(): crash DURING tool_node itself ──────────────────────────────
#
# The first test above crashes *after* tool_node commits — it never exercises
# a checkpoint whose "next" node is tool_node itself, so it never proves two
# things this task also needs proof of:
#   1. pending_tool_calls (a list of ToolCall dataclass instances) survives a
#      round-trip through the sqlite checkpoint's serializer and comes back
#      usable (tc.name / tc.args) on resume — untested by the other test,
#      since there pending_tool_calls is already consumed (set to None) by
#      the time the crash happens.
#   2. the trace-write batching fix (see tool_node's docstring) actually
#      prevents duplicate trace entries across a crash-mid-loop + resume,
#      not just in principle.
#
# Two parallel tool calls are queued (retrieve + web_search); the first fails
# normally (caught by tool_node's `except Exception`, queued into the node's
# local trace_events list) and the second raises KeyboardInterrupt —
# BaseException, not Exception, so it escapes tool_node's per-call try/except
# entirely and aborts the node's superstep before it ever reaches the batched
# `for event in trace_events: _trace_write(event)` flush. That's the crash
# point the old inline-write code would have already persisted the first
# tool's trace entry at, before losing the rest of the node to the crash.

def test_resume_reconstructs_pending_tool_calls_and_trace_not_duplicated():
    thread_id = f"w5t4-resume-tool-node-{uuid.uuid4().hex[:8]}"
    classify_calls: list[str] = []

    def _route(q: str) -> str:
        classify_calls.append(q)
        return "agent"

    def _complete_one_shot(messages, tools=None, system=None):
        return LLMResponse(
            text=None,
            tool_calls=[
                ToolCall(name="retrieve", args={"query": "q"}),
                ToolCall(name="web_search", args={"query": "q"}),
            ],
            raw={},
        )

    trace_write_mock = MagicMock()

    with patch("agent.graph.route", side_effect=_route), \
         patch("agent.graph.complete", side_effect=_complete_one_shot), \
         patch("agent.graph._trace_write", trace_write_mock), \
         patch("agent.tools.retrieve.RetrieveTool.run", side_effect=RuntimeError("retrieve failed")), \
         patch("agent.tools.web_search.WebSearchTool.run", side_effect=KeyboardInterrupt()):
        with pytest.raises(KeyboardInterrupt):
            list(stream_graph("q", kb_id=1, thread_id=thread_id))

    # The crash happened inside tool_node's loop, after retrieve's failure was
    # queued into the node's *local* trace_events list but before the node
    # could flush it — nothing should have been persisted.
    assert trace_write_mock.call_count == 0

    state = agent_graph._compiled_graph.get_state({"configurable": {"thread_id": thread_id}})
    assert state.next == ("tool_node",)

    def _complete_final(messages, tools=None, system=None):
        return _text("Answer despite tool failures.")

    with patch("agent.graph.route", side_effect=_route), \
         patch("agent.graph.complete", side_effect=_complete_final), \
         patch("agent.graph._trace_write", trace_write_mock), \
         patch("agent.graph._check_groundedness", return_value=_grounded_ok()), \
         patch("agent.tools.retrieve.RetrieveTool.run", side_effect=RuntimeError("retrieve failed")), \
         patch("agent.tools.web_search.WebSearchTool.run", side_effect=RuntimeError("web_search failed")):
        final_state = resume_graph(thread_id)

    assert classify_calls == ["q"]  # classify_node never re-ran

    # tool_node re-ran exactly once on resume (this IS the interrupted
    # superstep, so it's expected to run — unlike classify_node/llm_node's
    # first call, which are earlier, already-committed supersteps). The
    # ToolCall instances it operated on came from the checkpoint: if
    # deserialization had produced plain dicts instead of ToolCall objects,
    # `tc.name`/`tc.args` access inside tool_node would have raised
    # AttributeError and this resume would never have reached a final answer.
    assert final_state["answer"] == "Answer despite tool failures."
    assert final_state["tool_fail_counts"] == {"retrieve": 1, "web_search": 1}

    # Exactly one trace_write per tool failure — not two (which duplication
    # from a naive full-loop replay would have produced: one from the phase
    # that crashed plus one from the resumed replay).
    assert trace_write_mock.call_count == 2
    logged_tools = sorted(call.args[0]["tool"] for call in trace_write_mock.call_args_list)
    assert logged_tools == ["retrieve", "web_search"]
