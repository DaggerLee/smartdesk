"""tests/test_graph_self_healing.py — LangGraph agent-path self-healing tests (W5 T2).

Mirrors tests/test_self_healing.py (legacy loop) with the same mocked complete()
sequences, run through agent.graph's llm_node/tool_node/rewrite_node/
groundedness_node instead of agent.loop.run_agent(), so pass/fail is a direct
regression check against the legacy mechanism outcomes.

Patches:
  agent.graph.complete             — the LLM call inside llm_node
  agent.graph.route                — router decision (forced to "agent")
  agent.graph._check_groundedness  — the imported groundedness judge
  agent.tools.retrieve.RetrieveTool.run — the ChromaDB retrieval call
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent.graph import run_graph
from llm.client import LLMResponse, ToolCall


# ── Helpers ────────────────────────────────────────────────────────────────────

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


def _grounded_fail(sentences: list[str] | None = None) -> dict:
    return {"supported": False, "unsupported_sentences": sentences or ["Invented claim."]}


@pytest.fixture(autouse=True)
def force_agent_route():
    with patch("agent.graph.route", return_value="agent"):
        yield


# ── Regression: normal path unchanged from T1 ─────────────────────────────────

def test_normal_path_matches_legacy_outcome():
    """retrieve (relevant) -> grounded final answer, no self-healing triggered."""
    complete_mock = MagicMock(side_effect=[
        _tool("retrieve", query="q"),
        _text("Well-grounded answer."),
    ])
    with patch("agent.graph.complete", complete_mock), \
         patch("agent.tools.retrieve.RetrieveTool.run", return_value=_retrieve_result(relevance_ok=True)), \
         patch("agent.graph._check_groundedness", return_value=_grounded_ok()):
        result = run_graph("q", kb_id=1)

    assert result["answer"] == "Well-grounded answer."
    assert result["grounded"] is True
    assert complete_mock.call_count == 2  # retrieve round + final answer, no revision


# ── Mechanism 1: tool error retry ─────────────────────────────────────────────

def test_tool_error_twice_injects_unavailable_notice():
    complete_mock = MagicMock(side_effect=[
        _tool("retrieve", query="q"),
        _tool("retrieve", query="q"),
        _text("Final answer."),
    ])
    with patch("agent.graph.complete", complete_mock), \
         patch("agent.tools.retrieve.RetrieveTool.run",
               side_effect=[RuntimeError("fail1"), RuntimeError("fail2")]), \
         patch("agent.graph._check_groundedness", return_value=_grounded_ok()):
        result = run_graph("q", kb_id=1)

    assert result["tool_fail_counts"] == {"retrieve": 2}
    third_call_msgs = complete_mock.call_args_list[2][0][0]
    parts_texts = [
        part.get("text", "")
        for msg in third_call_msgs if msg["role"] == "user"
        for part in msg["parts"]
    ]
    assert any("no longer available" in t for t in parts_texts)


# ── Mechanism 2: low retrieval relevance rewrite ──────────────────────────────

def test_low_relevance_rewrite_caps_at_one():
    complete_mock = MagicMock(side_effect=[
        _tool("retrieve", query="q1"),  # hint injected (rewrite_count -> 1)
        _tool("retrieve", query="q2"),  # capped — no further hint
        _text("Answer."),
    ])
    with patch("agent.graph.complete", complete_mock), \
         patch("agent.tools.retrieve.RetrieveTool.run", return_value=_retrieve_result(relevance_ok=False)), \
         patch("agent.graph._check_groundedness", return_value=_grounded_ok()):
        result = run_graph("q", kb_id=1)

    assert result["rewrite_count"] == 1
    third_call_msgs = complete_mock.call_args_list[2][0][0]
    rewrite_hint_count = sum(
        part.get("text", "").count("Rewrite")
        for msg in third_call_msgs if msg["role"] == "user"
        for part in msg["parts"]
    )
    assert rewrite_hint_count == 1


# ── Mechanism 3: groundedness revision ────────────────────────────────────────

def test_groundedness_revises_on_fail():
    complete_mock = MagicMock(side_effect=[
        _tool("retrieve", query="q"),
        _text("Original answer with invented claim."),
        _text("Revised answer, properly grounded."),
    ])
    with patch("agent.graph.complete", complete_mock), \
         patch("agent.tools.retrieve.RetrieveTool.run", return_value=_retrieve_result(relevance_ok=True)), \
         patch("agent.graph._check_groundedness", side_effect=[_grounded_fail(), _grounded_ok()]):
        result = run_graph("q", kb_id=1)

    assert result["answer"] == "Revised answer, properly grounded."
    assert result["grounded"] is True
    assert result["revision_count"] == 1
    assert complete_mock.call_count == 3


# ── Boundary: max_turns wrap-up bypasses groundedness ─────────────────────────

def test_max_turns_wrap_up_skips_groundedness(monkeypatch):
    monkeypatch.setattr("agent.graph.MAX_AGENT_TURNS", 2)
    complete_mock = MagicMock(side_effect=[
        _tool("retrieve", query="q"),  # turn 0 -> 1
        _tool("retrieve", query="q"),  # turn 1 -> 2, hits cap
        _text("Wrap-up answer."),      # forced tools=None call, no groundedness check
    ])
    groundedness_mock = MagicMock(return_value=_grounded_ok())
    with patch("agent.graph.complete", complete_mock), \
         patch("agent.tools.retrieve.RetrieveTool.run", return_value=_retrieve_result(relevance_ok=True)), \
         patch("agent.graph._check_groundedness", groundedness_mock):
        result = run_graph("q", kb_id=1)

    assert result["answer"] == "Wrap-up answer."
    assert result["grounded"] is None
    groundedness_mock.assert_not_called()
