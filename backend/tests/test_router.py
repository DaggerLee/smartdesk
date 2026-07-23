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


def test_explicit_persist_intent_fails_over_to_agent_on_protocol_error(router_mock):
    router_mock.side_effect = LLMProtocolError(
        "Gemini response schema invalid: candidates missing"
    )

    assert route("Save this as a Markdown file titled Smoke") == "agent"
