"""Event-sequence tests for agent/loop.py.

Key patching note: loop.py uses `from llm.client import complete`, which binds
the name locally at import time. Patching `llm.client.complete` has no effect
on the loop — we must patch `agent.loop.complete` instead.

Tool backends are patched at the module-level name they're bound to:
  - chroma_client.query_documents  (accessed via module ref in retrieve.py)
  - agent.tools.web_search.WebSearchTool.run  (patched to avoid real network calls)
"""

from unittest.mock import MagicMock, patch

import pytest

from agent.loop import run_agent, AgentEvent
from llm.client import LLMResponse, ToolCall


# ── Helpers ───────────────────────────────────────────────────────────────────

def _text(t: str) -> LLMResponse:
    return LLMResponse(text=t, tool_calls=[], raw={})


def _tool(name: str, **args) -> LLMResponse:
    return LLMResponse(text=None, tool_calls=[ToolCall(name=name, args=args)], raw={})


def _run(query="q", kb_id=1, **kwargs) -> list[AgentEvent]:
    return list(run_agent(query, kb_id, **kwargs))


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def loop_seq():
    """Patch agent.loop.complete (local binding) with a response sequence."""
    mock = MagicMock()

    def _configure(responses: list[LLMResponse]) -> None:
        mock.side_effect = list(responses)

    with patch("agent.loop.complete", mock):
        yield _configure


@pytest.fixture
def mock_tools():
    """Stub tool backends so no network or DB calls are made."""
    with patch("chroma_client.query_documents", return_value=[]), \
         patch("agent.tools.web_search.WebSearchTool.run", return_value={"results": [], "evidence": []}):
        yield


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_two_tool_calls_then_final(loop_seq, mock_tools):
    """retrieve → web_search → final: correct event order and final text."""
    loop_seq([
        _tool("retrieve", query="q"),
        _tool("web_search", query="q"),
        _text("Final answer."),
    ])
    with patch("agent.loop._check_groundedness", return_value={"supported": True, "unsupported_sentences": []}):
        events = _run()
    assert [e.type for e in events] == [
        "tool_call", "tool_result",
        "tool_call", "tool_result",
        "final",
    ]
    assert events[-1].data["text"] == "Final answer."


def test_parallel_tool_calls_share_one_user_message(loop_seq, mock_tools):
    """Two functionCalls in one model turn → all functionResponses collected
    into a single user message (Gemini parallel-call contract), and roles
    alternate model/user with no consecutive user messages."""
    loop_seq([
        LLMResponse(
            text=None,
            tool_calls=[
                ToolCall("retrieve", {"query": "concept A"}),
                ToolCall("retrieve", {"query": "concept B"}),
            ],
            raw={},
        ),
        _text("Final answer."),
    ])
    with patch("agent.loop._check_groundedness", return_value={"supported": True, "unsupported_sentences": []}):
        events = _run()

    assert [e.type for e in events] == [
        "tool_call", "tool_result",
        "tool_call", "tool_result",
        "final",
    ]

    messages = events[-1].data["messages"]
    # [user query, model (2 functionCalls), user (2 functionResponses)]
    roles = [m["role"] for m in messages]
    assert roles == ["user", "model", "user"]

    model_calls = [p for p in messages[1]["parts"] if "functionCall" in p]
    fr_parts = [p for p in messages[2]["parts"] if "functionResponse" in p]
    assert len(model_calls) == 2
    assert len(fr_parts) == 2


def test_max_turns_forces_wrap_up(loop_seq, mock_tools):
    """After max_turns tool calls the loop exits and collects a wrap-up answer."""
    loop_seq([
        _tool("retrieve", query="q"),  # turn 0
        _tool("retrieve", query="q"),  # turn 1 → exits loop (max_turns=2)
        _text("Wrap-up answer."),      # collect call with tools=None
    ])
    events = _run(max_turns=2)
    assert [e.type for e in events] == [
        "tool_call", "tool_result",
        "tool_call", "tool_result",
        "final",
    ]
    assert events[-1].data["text"] == "Wrap-up answer."


def test_unknown_tool_yields_failed_event(loop_seq, mock_tools):
    """An unregistered tool name yields a failed tool_result event (W2 self-healing)."""
    loop_seq([
        LLMResponse(text=None, tool_calls=[ToolCall("nonexistent", {})], raw={}),
        LLMResponse(text="Fallback answer.", tool_calls=[], raw={}),
    ])
    with patch("agent.loop._check_groundedness", return_value={"supported": True, "unsupported_sentences": []}):
        events = _run()

    tool_results = [e for e in events if e.type == "tool_result"]
    assert len(tool_results) == 1
    assert tool_results[0].data["failed"] is True
    assert "nonexistent" in tool_results[0].data["name"]
    finals = [e for e in events if e.type == "final"]
    assert len(finals) == 1
