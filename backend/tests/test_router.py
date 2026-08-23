"""Tests for agent/router.py.

Patches agent.router.complete (local binding) so no real API calls are made.
"""

from unittest.mock import MagicMock, patch

import pytest

from agent.router import route
from llm.client import LLMProtocolError, LLMResponse


def _resp(text: str) -> LLMResponse:
    return LLMResponse(text=text, tool_calls=[], raw={})


@pytest.fixture
def router_mock():
    mock = MagicMock()
    with patch("agent.router.complete", mock):
        yield mock


# ── Normal classification ─────────────────────────────────────────────────────

def test_route_direct(router_mock):
    router_mock.return_value = _resp("direct")
    assert route("Hi!") == "direct"


def test_route_rag(router_mock):
    router_mock.return_value = _resp("rag")
    assert route("What is LoRA?") == "rag"


def test_route_agent(router_mock):
    router_mock.return_value = _resp("agent")
    assert route("Compare LoRA and QLoRA in detail") == "agent"


# ── Parse fallbacks ───────────────────────────────────────────────────────────

def test_route_verbose_label_falls_back(router_mock):
    """Model outputs extra words around the label — substring match still works."""
    router_mock.return_value = _resp("分类：rag")
    assert route("anything") == "rag"


def test_route_unknown_text_defaults_to_rag(router_mock):
    """Completely unrecognised output falls back to 'rag'."""
    router_mock.return_value = _resp("I cannot determine the category")
    assert route("anything") == "rag"


def test_explicit_persist_intent_overrides_model_rag_label(router_mock):
    router_mock.return_value = _resp("rag")

    assert route("Save this as a Markdown file titled Smoke") == "agent"

def test_source_bound_fact_invariant_lives_in_router_prompt(router_mock):
    router_mock.return_value = _resp("rag")

    query = "MCP 笔记为什么说 JWT 和 OAuth 不是竞品，什么时候才需要 OAuth？"
    assert route(query) == "rag"
    system = router_mock.call_args.kwargs["system"]
    assert "Source-bound single-fact" in system
    assert "same-topic technical-difference" in system
    assert "corpus-bounded absence-check" in system
    assert query in system
    assert "Streamable HTTP 和旧 HTTP+SSE transport 的关键差异是什么？" in system
    assert "PostgreSQL 的 transaction isolation levels 在这四份笔记里怎么调优？" in system


def test_cross_source_common_principle_agent_label_stays_agent(router_mock):
    router_mock.return_value = _resp("agent")

    assert route("把 MCP 三原语控制权分离和 task-scoped tools 放在一起看，它们共同的设计原则是什么？") == "agent"


def test_multi_agent_comparison_agent_label_stays_agent(router_mock):
    router_mock.return_value = _resp("agent")

    assert route("Multi-Agent 架构什么时候比单 Agent 更合适？") == "agent"


def test_parallel_lookup_agent_label_stays_agent(router_mock):
    router_mock.return_value = _resp("agent")

    assert route("同时查一下 Reflection 和 Planning 分别是什么？") == "agent"


def test_explicit_persist_intent_fails_over_to_agent_on_protocol_error(router_mock):
    router_mock.side_effect = LLMProtocolError(
        "Gemini response schema invalid: candidates missing"
    )

    assert route("Save this as a Markdown file titled Smoke") == "agent"
