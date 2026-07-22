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
from typing import Iterable, Iterator, TypeVar

import config

_log_path = Path(config.TRACE_LOG_PATH)

# Correlates every trace line written during a unit of work (one eval item,
# one chat request) without threading an id through every function signature
# in the call chain (router/complete/retrieve/groundedness/...). Captured
# per coroutine and thread execution context. Synchronous SSE generators must
# use iterate_with_context() because Starlette may advance successive chunks
# in different worker contexts.
_context: contextvars.ContextVar[dict] = contextvars.ContextVar("_trace_context", default={})
_T = TypeVar("_T")


@contextmanager
def context(**fields):
    """Merge fields (e.g. item_id, request_id) into every trace entry written
    inside this block, including from nested calls."""
    current = _context.get()
    if all(key in current and current[key] == value for key, value in fields.items()):
        yield
        return
    token = _context.set({**_context.get(), **fields})
    try:
        yield
    finally:
        _context.reset(token)


def iterate_with_context(iterable: Iterable[_T], **fields) -> Iterator[_T]:
    """Advance each synchronous iterator step inside a fresh trace context.

    Starlette may advance successive SSE chunks in different worker contexts.
    The context is therefore reset before yielding each produced item instead
    of spanning the iterator's own ``yield`` boundary.
    """
    iterator = iter(iterable)
    while True:
        token = _context.set({**_context.get(), **fields})
        try:
            item = next(iterator)
        except StopIteration:
            return
        finally:
            _context.reset(token)
        yield item


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
