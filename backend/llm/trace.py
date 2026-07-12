"""Structured JSONL trace logger for LLM and tool calls.

One line per call, written to TRACE_LOG_PATH (default: logs/traces/traces.jsonl).
Never raises — tracing failures must not affect the main request flow.
"""

import contextvars
import json
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import config

_log_path = Path(config.TRACE_LOG_PATH)

# Correlates every trace line written during a unit of work (one eval item,
# one chat request) without threading an id through every function signature
# in the call chain (router/complete/retrieve/groundedness/...). Captured
# per-generator/coroutine by Python's contextvars, so it survives across
# `yield` in SSE generator functions as long as the generator object is
# created while the context is active.
_context: contextvars.ContextVar[dict] = contextvars.ContextVar("_trace_context", default={})


@contextmanager
def context(**fields):
    """Merge fields (e.g. item_id, request_id) into every trace entry written
    inside this block, including from nested calls."""
    token = _context.set({**_context.get(), **fields})
    try:
        yield
    finally:
        _context.reset(token)


def write(entry: dict) -> None:
    """Public alias — use for one-shot writes outside a span."""
    _write(entry)


def _write(entry: dict) -> None:
    try:
        _log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {**_context.get(), **entry}
        entry.setdefault("ts", datetime.now(timezone.utc).isoformat())
        with open(_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


@contextmanager
def span(entry_template: dict):
    """Context manager: measures latency and writes one trace line on exit.

    Usage:
        with trace.span({"type": "tool_call", "tool": "retrieve"}) as out:
            result = do_work()
            out["evidence_count"] = len(result["evidence"])
        # trace line written automatically with latency_ms

    Mutate the yielded dict inside the block to add output fields.
    The line is always written (even if an exception is raised).
    """
    t0 = time.monotonic()
    try:
        yield entry_template
    finally:
        entry_template["latency_ms"] = int((time.monotonic() - t0) * 1000)
        _write(entry_template)
