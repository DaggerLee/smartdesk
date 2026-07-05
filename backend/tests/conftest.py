"""Shared pytest fixtures.

mock_llm_text  — complete() returns a plain-text LLMResponse
mock_llm_tool  — complete() returns a functionCall LLMResponse (retrieve)
mock_llm_seq   — complete() returns a configurable sequence of responses
"""

from unittest.mock import MagicMock, patch

import pytest

from llm.client import LLMResponse, ToolCall


@pytest.fixture
def mock_llm_text():
    """Fixture: every complete() call returns a plain text response."""
    response = LLMResponse(text="Test answer.", tool_calls=[], raw={})
    with patch("llm.client.complete", return_value=response) as mock:
        yield mock


@pytest.fixture
def mock_llm_tool():
    """Fixture: complete() returns a retrieve tool call."""
    response = LLMResponse(
        text=None,
        tool_calls=[ToolCall(name="retrieve", args={"query": "test query"})],
        raw={},
    )
    with patch("llm.client.complete", return_value=response) as mock:
        yield mock


@pytest.fixture
def mock_llm_seq():
    """Fixture: complete() returns responses from a list in order.

    Usage:
        def test_foo(mock_llm_seq):
            mock_llm_seq([
                LLMResponse(text=None, tool_call=ToolCall("retrieve", {"query": "q"}), raw={}),
                LLMResponse(text="Final answer.", tool_call=None, raw={}),
            ])
    """
    mock = MagicMock()
    responses: list[LLMResponse] = []

    def _configure(resp_list: list[LLMResponse]) -> None:
        responses.clear()
        responses.extend(resp_list)
        mock.side_effect = responses

    with patch("llm.client.complete", mock):
        yield _configure
