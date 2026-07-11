"""Secret-hygiene tests for llm/client.py.

requests puts the full URL — including ?key=… — into every HTTPError message,
and those strings end up in eval result files (ItemResult.error) and pasted
logs. These tests assert that exceptions raised by the client never contain
the API key in plaintext.
"""

from unittest.mock import patch

import pytest
import requests

import llm.client as client


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
    monkeypatch.setattr(client, "_cached_model", "models/test-model")
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


def test_complete_connection_error_has_no_key(monkeypatch):
    monkeypatch.setattr(client, "_cached_model", "models/test-model")
    monkeypatch.setattr(client.config, "GEMINI_API_KEY", "SECRET123")
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
