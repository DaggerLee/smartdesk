"""Secret-hygiene tests for llm/client.py.

requests puts the full URL — including ?key=… — into every HTTPError message,
and those strings end up in eval result files (ItemResult.error) and pasted
logs. These tests assert that exceptions raised by the client never contain
the API key in plaintext.
"""

from contextlib import contextmanager
from unittest.mock import patch

import pytest
import requests

import llm.client as client


@contextmanager
def _capture_span(entries: list[dict]):
    entry: dict = {}
    entries.append(entry)
    yield entry


def _fake_response(status: int, url: str) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status
    resp.url = url
    return resp


def test_redact_masks_key_param():
    msg = (
        "403 Client Error: Forbidden for url: "
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "m:generateContent?key=SECRET123&alt=sse"
    )
    out = client._redact(msg)
    assert "SECRET123" not in out
    assert "key=***" in out
    assert "&alt=sse" in out  # only the key param is masked


def test_complete_http_error_has_no_key(monkeypatch):
    monkeypatch.setattr(client, "_model_validated", True)
    monkeypatch.setattr(client.config, "GEMINI_API_KEY", "SECRET123")
    fake = _fake_response(
        403,
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "test-model:generateContent?key=SECRET123",
    )
    with patch("llm.client.requests.post", return_value=fake):
        with pytest.raises(requests.HTTPError) as ei:
            client.complete(messages=[{"role": "user", "parts": [{"text": "hi"}]}])
    assert "SECRET123" not in str(ei.value)
    assert "key=***" in str(ei.value)


def test_model_turn_preserves_thought_signature():
    raw = {"candidates": [{"content": {"role": "model", "parts": [
        {"functionCall": {"name": "retrieve", "args": {"query": "q"}},
         "thoughtSignature": "sig123"},
    ]}}]}
    resp = client.LLMResponse(
        text=None, tool_calls=[client.ToolCall("retrieve", {"query": "q"})], raw=raw,
    )
    turn = client.model_turn(resp)
    assert turn["role"] == "model"
    assert turn["parts"][0]["thoughtSignature"] == "sig123"


def test_model_turn_falls_back_to_reconstruction():
    resp = client.LLMResponse(
        text=None, tool_calls=[client.ToolCall("retrieve", {"query": "q"})], raw={},
    )
    turn = client.model_turn(resp)
    assert turn == {
        "role": "model",
        "parts": [{"functionCall": {"name": "retrieve", "args": {"query": "q"}}}],
    }


def test_complete_connection_error_has_no_key(monkeypatch):
    monkeypatch.setattr(client, "_model_validated", True)
    monkeypatch.setattr(client.config, "GEMINI_API_KEY", "SECRET123")
    monkeypatch.setattr(client.time, "sleep", lambda _s: None)
    err = requests.ConnectionError(
        "Failed to establish a new connection to "
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "test-model:generateContent?key=SECRET123"
    )
    with patch("llm.client.requests.post", side_effect=err):
        with pytest.raises(requests.ConnectionError) as ei:
            client.complete(messages=[{"role": "user", "parts": [{"text": "hi"}]}])
    assert "SECRET123" not in str(ei.value)
    assert "key=***" in str(ei.value)


def test_complete_retries_on_timeout_then_succeeds(monkeypatch):
    """A Timeout/ConnectionError counts against the same retry budget as a
    transient HTTP status (429/500/503/504) rather than propagating
    immediately, since it's equally a transient failure the caller shouldn't
    have to handle itself."""
    monkeypatch.setattr(client, "_model_validated", True)
    monkeypatch.setattr(client.config, "GEMINI_API_KEY", "SECRET123")
    monkeypatch.setattr(client, "_throttle", lambda: None)  # isolate from LLM_MIN_INTERVAL_S set by other test modules
    sleeps: list[float] = []
    monkeypatch.setattr(client.time, "sleep", lambda s: sleeps.append(s))

    ok = _fake_response(200, "https://example.com")
    ok._content = b'{"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}'

    calls = {"n": 0}

    def _flaky_post(url, **kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.exceptions.Timeout("Read timed out (timeout=30)")
        return ok

    with patch("llm.client.requests.post", side_effect=_flaky_post):
        resp = client.complete(messages=[{"role": "user", "parts": [{"text": "hi"}]}])

    assert resp.text == "hi"
    assert calls["n"] == 3
    assert len(sleeps) == 2  # retried twice before the successful 3rd attempt


def test_complete_gives_up_after_retry_budget_on_connection_error(monkeypatch):
    monkeypatch.setattr(client, "_model_validated", True)
    monkeypatch.setattr(client.config, "GEMINI_API_KEY", "SECRET123")
    monkeypatch.setattr(client, "_throttle", lambda: None)  # isolate from LLM_MIN_INTERVAL_S set by other test modules
    sleeps: list[float] = []
    monkeypatch.setattr(client.time, "sleep", lambda s: sleeps.append(s))

    with patch("llm.client.requests.post", side_effect=requests.exceptions.ConnectionError("boom")):
        with pytest.raises(requests.exceptions.ConnectionError):
            client.complete(messages=[{"role": "user", "parts": [{"text": "hi"}]}])

    assert len(sleeps) == 5  # attempts 0-4 retry (5 sleeps), attempt 5 raises


def test_complete_candidate_less_200_raises_safe_protocol_error(monkeypatch):
    monkeypatch.setattr(client, "_model_validated", True)
    monkeypatch.setattr(client.config, "GEMINI_API_KEY", "SECRET123")
    response = _fake_response(200, "https://example.com")
    response._content = (
        b'{"promptFeedback":{"blockReason":"OTHER"},'
        b'"sensitiveBody":"DO_NOT_LOG_THIS"}'
    )

    traces: list[dict] = []
    monkeypatch.setattr(client, "_trace_span", lambda _entry: _capture_span(traces))

    with patch("llm.client.requests.post", return_value=response):
        with pytest.raises(client.LLMProtocolError) as error:
            client.complete(
                messages=[{"role": "user", "parts": [{"text": "persist"}]}]
            )

    message = str(error.value)
    assert "candidates" in message
    assert "SECRET123" not in message
    assert "DO_NOT_LOG_THIS" not in message
    assert error.value.diagnostics == {
        "candidate_count": 0,
        "finish_reason": None,
        "prompt_block_reason": "OTHER",
        "content_present": False,
        "parts_present": False,
        "parts_count": 0,
    }
    assert traces == [{"protocol_error": error.value.diagnostics}]


def test_complete_content_less_candidate_raises_safe_protocol_error(monkeypatch):
    monkeypatch.setattr(client, "_model_validated", True)
    monkeypatch.setattr(client.config, "GEMINI_API_KEY", "SECRET123")
    response = _fake_response(200, "https://example.com")
    response._content = (
        b'{"candidates":[{"finishReason":"STOP"}],'
        b'"sensitiveBody":"DO_NOT_LOG_THIS"}'
    )

    traces: list[dict] = []
    monkeypatch.setattr(client, "_trace_span", lambda _entry: _capture_span(traces))

    with patch("llm.client.requests.post", return_value=response):
        with pytest.raises(client.LLMProtocolError) as error:
            client.complete(
                messages=[{"role": "user", "parts": [{"text": "persist"}]}]
            )

    message = str(error.value)
    assert "content" in message
    assert "DO_NOT_LOG_THIS" not in message
    assert "SECRET123" not in message
    assert error.value.diagnostics == {
        "candidate_count": 1,
        "finish_reason": "STOP",
        "prompt_block_reason": None,
        "content_present": False,
        "parts_present": False,
        "parts_count": 0,
    }
    assert traces == [{"protocol_error": error.value.diagnostics}]


def test_protocol_diagnostics_only_include_shape_metadata(monkeypatch):
    monkeypatch.setattr(client, "_model_validated", True)
    monkeypatch.setattr(client.config, "GEMINI_API_KEY", "SECRET123")
    response = _fake_response(200, "https://example.com")
    response._content = (
        b'{"candidates":[{"finishReason":"DO_NOT_LOG_FINISH"}],'
        b'"promptFeedback":{"blockReason":"DO_NOT_LOG_BLOCK"},'
        b'"sensitiveBody":"DO_NOT_LOG_THIS"}'
    )
    traces: list[dict] = []
    monkeypatch.setattr(client, "_trace_span", lambda _entry: _capture_span(traces))

    with patch("llm.client.requests.post", return_value=response):
        with pytest.raises(client.LLMProtocolError) as error:
            client.complete(
                messages=[{"role": "user", "parts": [{"text": "SECRET NOTE BODY"}]}]
            )

    assert error.value.diagnostics["finish_reason"] == "UNKNOWN"
    assert error.value.diagnostics["prompt_block_reason"] == "UNKNOWN"
    serialized_trace = str(traces)
    assert "SECRET NOTE BODY" not in serialized_trace
    assert "DO_NOT_LOG_FINISH" not in serialized_trace
    assert "DO_NOT_LOG_BLOCK" not in serialized_trace
    assert "DO_NOT_LOG_THIS" not in serialized_trace
