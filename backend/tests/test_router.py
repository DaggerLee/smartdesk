"""Tests for agent/router.py.

Patches agent.router.complete (local binding) so no real API calls are made.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.router import SYSTEM_PROMPT, route
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

    query = "某份设备手册里，保修范围和免责条款分别覆盖什么？"
    assert route(query) == "rag"
    system = router_mock.call_args.kwargs["system"]
    assert "Source-bound single-fact" in system
    assert "same-topic technical-difference" in system
    assert "corpus-bounded absence-check" in system
    assert query in system
    assert "同一个 SDK 文档里，本地缓存和远程同步的关键差异是什么？" in system
    assert "这组项目笔记里有没有讲电子表格宏安全策略？" in system


def test_router_prompt_does_not_embed_gold_or_holdout_queries():
    eval_dir = Path("eval")
    gold_queries = set()
    for path in [eval_dir / "gold_set.jsonl", *eval_dir.glob("holdout_set_*.jsonl")]:
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            gold_queries.add(record["query"])

    leaked = sorted(query for query in gold_queries if query in SYSTEM_PROMPT)
    assert leaked == []


def test_cross_source_common_principle_agent_label_stays_agent(router_mock):
    router_mock.return_value = _resp("agent")

    assert route("把 MCP 三原语控制权分离和 task-scoped tools 放在一起看，它们共同的设计原则是什么？") == "agent"


def test_cross_source_shared_risk_question_lives_in_router_prompt(router_mock):
    router_mock.return_value = _resp("agent")

    query = "Tina 的 traceability 复现文档和 Vibe 的 checkpoint 共同降低什么工程风险？"
    assert route(query) == "agent"
    system = router_mock.call_args.kwargs["system"]
    assert query in system
    assert "Requires synthesis across two named concepts" in system


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
