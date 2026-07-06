"""tests/test_self_healing.py — W2 self-healing mechanism tests.

Patches:
  agent.loop.complete              — the LLM call inside run_agent()
  agent.loop._check_groundedness   — the imported groundedness judge
  agent.tools.retrieve.RetrieveTool.run — the ChromaDB retrieval call
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent.loop import AgentEvent, run_agent
from llm.client import LLMResponse, ToolCall


# ── Helpers ────────────────────────────────────────────────────────────────────

def _resp(text: str = "", tool_calls: list | None = None) -> LLMResponse:
    return LLMResponse(text=text or None, tool_calls=tool_calls or [], raw={})


def _tc(name: str, args: dict | None = None) -> ToolCall:
    return ToolCall(name=name, args=args or {})


def _retrieve_result(relevance_ok: bool = True) -> dict:
    return {
        "chunks": ["chunk text"],
        "evidence": [{"text": "chunk text", "source": "doc.pdf"}],
        "relevance_ok": relevance_ok,
    }


def _grounded_ok() -> dict:
    return {"supported": True, "unsupported_sentences": []}


def _grounded_fail(sentences: list[str] | None = None) -> dict:
    return {"supported": False, "unsupported_sentences": sentences or ["Invented claim."]}


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_groundedness_ok():
    with patch("agent.loop._check_groundedness", return_value=_grounded_ok()):
        yield


@pytest.fixture
def mock_groundedness_fail_then_ok():
    with patch("agent.loop._check_groundedness", side_effect=[_grounded_fail(), _grounded_ok()]):
        yield


# ── Mechanism 1: Tool error retry ─────────────────────────────────────────────

def test_tool_error_retry_once(mock_groundedness_ok):
    """Single tool exception yields a failed tool_result and loop continues to answer."""
    with patch("agent.tools.retrieve.RetrieveTool.run", side_effect=RuntimeError("DB failed")):
        loop_complete = MagicMock(side_effect=[
            _resp(tool_calls=[_tc("retrieve", {"query": "q"})]),
            _resp(text="Answer based on no evidence."),
        ])
        with patch("agent.loop.complete", loop_complete):
            events = list(run_agent("q", kb_id=1))

    tool_results = [e for e in events if e.type == "tool_result"]
    finals = [e for e in events if e.type == "final"]

    assert len(tool_results) == 1
    assert tool_results[0].data["failed"] is True
    assert len(finals) == 1
    assert loop_complete.call_count == 2  # one tool round + one final answer


def test_tool_error_twice_injects_unavailable(mock_groundedness_ok):
    """Two consecutive failures inject the unavailability notice into the message."""
    with patch("agent.tools.retrieve.RetrieveTool.run",
               side_effect=[RuntimeError("fail1"), RuntimeError("fail2")]):
        loop_complete = MagicMock(side_effect=[
            _resp(tool_calls=[_tc("retrieve", {"query": "q"})]),
            _resp(tool_calls=[_tc("retrieve", {"query": "q"})]),
            _resp(text="Final answer."),
        ])
        with patch("agent.loop.complete", loop_complete):
            list(run_agent("q", kb_id=1))

    # The third complete() call receives all accumulated messages; the second
    # error's functionResponse parts should carry the unavailability notice.
    third_call_msgs = loop_complete.call_args_list[2][0][0]
    parts_texts = [
        part.get("text", "")
        for msg in third_call_msgs if msg["role"] == "user"
        for part in msg["parts"]
    ]
    assert any("no longer available" in t for t in parts_texts)


# ── Mechanism 2: Low retrieval relevance ──────────────────────────────────────

def test_low_relevance_triggers_rewrite_hint(mock_groundedness_ok):
    """retrieve returning relevance_ok=False appends the rewrite hint once."""
    with patch("agent.tools.retrieve.RetrieveTool.run",
               return_value=_retrieve_result(relevance_ok=False)):
        loop_complete = MagicMock(side_effect=[
            _resp(tool_calls=[_tc("retrieve", {"query": "original query"})]),
            _resp(text="Answer."),
        ])
        with patch("agent.loop.complete", loop_complete):
            list(run_agent("original query", kb_id=1))

    # Second complete() call sees the hint appended to the functionResponse parts.
    second_call_msgs = loop_complete.call_args_list[1][0][0]
    parts_texts = [
        part.get("text", "")
        for msg in second_call_msgs if msg["role"] == "user"
        for part in msg["parts"]
    ]
    assert any("Rewrite" in t for t in parts_texts)


def test_low_relevance_no_second_rewrite(mock_groundedness_ok):
    """After _MAX_REWRITES low-relevance results, no further hints are injected."""
    with patch("agent.tools.retrieve.RetrieveTool.run",
               return_value=_retrieve_result(relevance_ok=False)):
        loop_complete = MagicMock(side_effect=[
            _resp(tool_calls=[_tc("retrieve", {"query": "q1"})]),  # hint injected (count=1)
            _resp(tool_calls=[_tc("retrieve", {"query": "q2"})]),  # capped — no hint
            _resp(text="Answer."),
        ])
        with patch("agent.loop.complete", loop_complete):
            list(run_agent("q", kb_id=1))

    # Third complete() call: exactly one "Rewrite" across all user message parts.
    third_call_msgs = loop_complete.call_args_list[2][0][0]
    rewrite_count = sum(
        part.get("text", "").count("Rewrite")
        for msg in third_call_msgs if msg["role"] == "user"
        for part in msg["parts"]
    )
    assert rewrite_count == 1


# ── Mechanism 3: Groundedness check ───────────────────────────────────────────

def test_groundedness_revises_on_fail(mock_groundedness_fail_then_ok):
    """When judge fails, answer is revised; final event reflects revised text."""
    with patch("agent.tools.retrieve.RetrieveTool.run",
               return_value=_retrieve_result(relevance_ok=True)):
        loop_complete = MagicMock(side_effect=[
            _resp(tool_calls=[_tc("retrieve", {"query": "q"})]),
            _resp(text="Original answer with invented claim."),
            _resp(text="Revised answer, properly grounded."),  # after revision prompt
        ])
        with patch("agent.loop.complete", loop_complete):
            events = list(run_agent("q", kb_id=1))

    finals = [e for e in events if e.type == "final"]
    assert len(finals) == 1
    assert finals[0].data["text"] == "Revised answer, properly grounded."
    assert finals[0].data["grounded"] is True   # second judge call returned ok
    assert loop_complete.call_count == 3          # retrieve + original + revision


def test_groundedness_pass(mock_groundedness_ok):
    """When judge passes, final event has grounded=True and no revision call."""
    with patch("agent.tools.retrieve.RetrieveTool.run",
               return_value=_retrieve_result(relevance_ok=True)):
        loop_complete = MagicMock(side_effect=[
            _resp(tool_calls=[_tc("retrieve", {"query": "q"})]),
            _resp(text="Well-grounded answer."),
        ])
        with patch("agent.loop.complete", loop_complete):
            events = list(run_agent("q", kb_id=1))

    finals = [e for e in events if e.type == "final"]
    assert len(finals) == 1
    assert finals[0].data["text"] == "Well-grounded answer."
    assert finals[0].data["grounded"] is True
    assert loop_complete.call_count == 2  # no revision call
