"""LLM client — thin wrapper around the Gemini REST API.

Separates transport from prompt construction so the agent loop and router can
call complete() / stream() directly without knowing about Gemini internals.
gemini_client.py is kept as-is for the existing v1 routes.
"""

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Iterator, Optional

import requests

import config
from llm.trace import span as _trace_span, write as _trace_write

logger = logging.getLogger(__name__)


# ── Response types ─────────────────────────────────────────────────────────────

@dataclass
class ToolCall:
    name: str
    args: dict


@dataclass
class LLMResponse:
    text: Optional[str]        # set when the model returns a text part
    tool_calls: list[ToolCall] # non-empty when the model returns functionCall parts
    raw: dict                  # full API response, used by trace logger


# ── Secret hygiene ────────────────────────────────────────────────────────────

def _redact(text: str) -> str:
    """Mask the API key query param so exception text never leaks secrets.

    requests puts the full URL (including ?key=…) into every HTTPError and
    connection-error message; those strings end up in eval result files and
    pasted logs, so they must be scrubbed at the raise site.
    """
    return re.sub(r"key=[^&\s\"']+", "key=***", text)


def _post(url: str, **kwargs) -> requests.Response:
    """requests.post with key-redacted exception messages."""
    try:
        return requests.post(url, **kwargs)
    except requests.RequestException as exc:
        raise type(exc)(_redact(str(exc))) from None


def _raise_for_status(resp: requests.Response) -> None:
    """resp.raise_for_status() with a key-redacted error message."""
    try:
        resp.raise_for_status()
    except requests.HTTPError as exc:
        raise requests.HTTPError(_redact(str(exc)), response=resp) from None


# ── Model validation ──────────────────────────────────────────────────────────

_model_validated = False


def _find_model() -> str:
    """Return config.GEMINI_MODEL, validating its availability once per process.

    The model name is single-sourced in config — this function never
    auto-picks. It only confirms the configured model exists for this API key
    and supports generateContent, so traces and eval archives always record
    exactly the model that was called.
    """
    global _model_validated
    if _model_validated:
        return config.GEMINI_MODEL

    try:
        resp = requests.get(
            f"{config.GEMINI_BASE_URL}/models?key={config.GEMINI_API_KEY}",
            timeout=15,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"ListModels request failed: {_redact(str(exc))}") from None
    if resp.status_code != 200:
        raise RuntimeError(f"ListModels failed ({resp.status_code}): {_redact(resp.text)}")

    methods_by_name = {
        m["name"]: m.get("supportedGenerationMethods", [])
        for m in resp.json().get("models", [])
    }
    methods = methods_by_name.get(config.GEMINI_MODEL)
    if methods is None:
        raise RuntimeError(
            f"Configured model {config.GEMINI_MODEL!r} not available for this "
            f"API key. Available: {sorted(methods_by_name)}"
        )
    if "generateContent" not in methods:
        raise RuntimeError(
            f"Configured model {config.GEMINI_MODEL!r} does not support generateContent."
        )
    logger.info(f"[llm] Validated model: {config.GEMINI_MODEL}")
    _model_validated = True
    return config.GEMINI_MODEL


# ── Global rate limiter ───────────────────────────────────────────────────────

_last_call_ts = 0.0


def _throttle() -> None:
    """Enforce a minimum interval between LLM requests, across ALL callers.

    Controlled by LLM_MIN_INTERVAL_S (default 0 = disabled). Eval runs set it
    to ~6 so router/judge/generate/groundedness calls can't burst past the
    free-tier RPM limit; production paths leave it unset and are unaffected.
    """
    global _last_call_ts
    min_interval = float(os.getenv("LLM_MIN_INTERVAL_S", "0"))
    if min_interval <= 0:
        return
    wait = _last_call_ts + min_interval - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_call_ts = time.monotonic()


# ── Public interface ──────────────────────────────────────────────────────────

def model_turn(resp: LLMResponse) -> dict:
    """Build the model-role history message for a tool-call response.

    Echoes the raw candidate content verbatim when available: thinking models
    (gemini-3.5+) attach a thoughtSignature to functionCall parts and reject
    replayed history that drops it, so the model turn must not be
    reconstructed from parsed tool calls. The reconstruction below is only a
    fallback for tests that use synthetic responses (raw={}).
    """
    try:
        content = resp.raw["candidates"][0]["content"]
        return {"role": content.get("role", "model"), "parts": content["parts"]}
    except (KeyError, IndexError, TypeError):
        return {
            "role": "model",
            "parts": [
                {"functionCall": {"name": tc.name, "args": tc.args}}
                for tc in resp.tool_calls
            ],
        }


def complete(
    messages: list[dict],
    tools: list[dict] | None = None,
    system: str | None = None,
    temperature: float | None = None,
) -> LLMResponse:
    """Non-streaming generation for use inside the agent loop.

    Args:
        messages: Gemini-format contents list,
                  e.g. [{"role": "user", "parts": [{"text": "..."}]}]
        tools:    list of Gemini functionDeclaration dicts; when provided the
                  model may return functionCall parts instead of text.
        system:   optional system instruction text; passed as systemInstruction
                  (does not occupy a conversation turn).

    Returns:
        LLMResponse with .tool_calls non-empty XOR .text set.
    """
    model = _find_model()
    url = f"{config.GEMINI_BASE_URL}/{model}:generateContent?key={config.GEMINI_API_KEY}"

    body: dict = {"contents": messages}
    if tools:
        body["tools"] = [{"functionDeclarations": tools}]
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if temperature is not None:
        body["generationConfig"] = {"temperature": temperature}

    _entry = {
        "type": "llm_complete",
        "model": model,
        "input_turns": len(messages),
        "has_tools": bool(tools),
        "has_system": bool(system),
    }
    with _trace_span(_entry) as _out:
        _delay = 30
        for _attempt in range(6):
            _throttle()
            try:
                resp = _post(url, json=body, timeout=30)
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                # No HTTP response at all (never got a status code) — same
                # transient-failure budget as a 429/503, since a flaky
                # connection is no more the caller's fault than an overloaded
                # server. _post() has already redacted the key in exc's message.
                if _attempt < 5:
                    logger.warning(f"[llm] {type(exc).__name__} transient, retrying in {_delay}s (attempt {_attempt+1}/5)")
                    time.sleep(_delay)
                    _delay = min(_delay * 2, 120)
                    continue
                raise
            if resp.status_code in (429, 500, 503, 504) and _attempt < 5:
                logger.warning(f"[llm] {resp.status_code} transient, retrying in {_delay}s (attempt {_attempt+1}/5)")
                time.sleep(_delay)
                _delay = min(_delay * 2, 120)
                continue
            _raise_for_status(resp)
            break
        data = resp.json()

        parts = data["candidates"][0]["content"].get("parts", [])

        tool_calls = [
            ToolCall(name=p["functionCall"]["name"], args=p["functionCall"].get("args", {}))
            for p in parts
            if "functionCall" in p
        ]
        if tool_calls:
            _out["tool_names"] = [tc.name for tc in tool_calls]
            _out["text_chars"] = None
            return LLMResponse(text=None, tool_calls=tool_calls, raw=data)

        text = "".join(p.get("text", "") for p in parts)
        _out["tool_names"] = []
        _out["text_chars"] = len(text) if text else 0
        return LLMResponse(text=text or None, tool_calls=[], raw=data)


def stream(messages: list[dict], system: str | None = None) -> Iterator[str]:
    """Streaming generation for the final answer output.

    Args:
        messages: Gemini-format contents list (no tools — final answer only).
        system:   optional system instruction text.

    Yields:
        Text chunks as they arrive from the API.
    """
    model = _find_model()
    url = f"{config.GEMINI_BASE_URL}/{model}:streamGenerateContent?alt=sse&key={config.GEMINI_API_KEY}"

    body: dict = {"contents": messages}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}

    chunks: list[str] = []
    with _post(
        url,
        json=body,
        stream=True,
        timeout=60,
    ) as resp:
        _raise_for_status(resp)
        for raw_line in resp.iter_lines():
            if not raw_line:
                continue
            line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
            if not line.startswith("data: "):
                continue
            data_str = line[6:].strip()
            if data_str == "[DONE]":
                break
            try:
                data = json.loads(data_str)
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                if text:
                    chunks.append(text)
                    yield text
            except (KeyError, json.JSONDecodeError, IndexError):
                continue

    _trace_write({
        "type": "llm_stream",
        "model": model,
        "input_turns": len(messages),
        "chunk_count": len(chunks),
        "total_chars": sum(len(c) for c in chunks),
    })
