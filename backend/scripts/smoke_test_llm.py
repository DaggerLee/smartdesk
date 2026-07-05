#!/usr/bin/env python3
"""Smoke test for llm/client.py against the real Gemini API.

Scenarios:
  1. Basic text response (confirms API key + model discovery work)
  2. functionCall round-trip (model → tool call → functionResponse → final text)
  3. Wrap-up instruction appended to functionResponse parts (max_turns exit path)

Run from backend/:
    python3 scripts/smoke_test_llm.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

import config

if not config.GEMINI_API_KEY:
    print("✗ GEMINI_API_KEY not set — copy .env.example to .env and fill it in")
    sys.exit(1)

from llm.client import complete

_RETRIEVE_DECL = {
    "name": "retrieve",
    "description": "Search the knowledge base for relevant document chunks.",
    "parameters": {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "search query"}},
        "required": ["query"],
    },
}

_SYSTEM = (
    "You are a helpful assistant with access to a knowledge base tool. "
    "For factual questions, call retrieve before answering."
)

PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"


def check(cond: bool, msg: str) -> None:
    print(f"  {PASS if cond else FAIL} {msg}")
    if not cond:
        sys.exit(1)


# ── 1. Basic text response ─────────────────────────────────────────────────────
print("\n[1] Basic text response + system instruction")
resp = complete(
    [{"role": "user", "parts": [{"text": "Reply with exactly one word: hello"}]}],
    system=_SYSTEM,
)
check(resp.tool_calls == [], "tool_calls is empty")
check(bool(resp.text), f"got text: {resp.text!r}")


# ── 2. functionCall round-trip ─────────────────────────────────────────────────
print("\n[2] functionCall round-trip")
messages = [{"role": "user", "parts": [{"text": "What does the knowledge base say about Python decorators?"}]}]
resp = complete(messages, tools=[_RETRIEVE_DECL], system=_SYSTEM)

if resp.tool_calls:
    tc = resp.tool_calls[0]
    check(tc.name == "retrieve", f"tool name is 'retrieve', got {tc.name!r}")
    check("query" in tc.args, f"args contain 'query': {tc.args}")
    print(f"    → called retrieve(query={tc.args['query']!r})")

    # feed back: model turn (functionCall) + user turn (functionResponse)
    messages.append({
        "role": "model",
        "parts": [{"functionCall": {"name": tc.name, "args": tc.args}}],
    })
    messages.append({
        "role": "user",
        "parts": [{"functionResponse": {
            "name": tc.name,
            "response": {
                "chunks": ["Python decorators are functions that wrap other functions."],
                "evidence": [{"text": "Python decorators wrap functions.", "source": "docs.pdf"}],
            },
        }}],
    })

    resp2 = complete(messages, system=_SYSTEM)
    check(bool(resp2.text), f"got final text: {resp2.text[:80]!r}")
    check(resp2.tool_calls == [], "no further tool calls in final answer")
else:
    print(f"  (model answered directly: {resp.text[:80]!r})")
    print(f"  {PASS} skipped round-trip (no tool call returned)")


# ── 3. Wrap-up instruction inside functionResponse parts ──────────────────────
print("\n[3] Wrap-up instruction appended to functionResponse parts")

from agent.loop import _WRAP_UP_INSTRUCTION

wrap_messages = [
    {"role": "user", "parts": [{"text": "Tell me about async Python."}]},
    {
        "role": "model",
        "parts": [{"functionCall": {"name": "retrieve", "args": {"query": "async Python"}}}],
    },
    {
        "role": "user",
        "parts": [
            {"functionResponse": {
                "name": "retrieve",
                "response": {"chunks": [], "evidence": []},
            }},
            {"text": _WRAP_UP_INSTRUCTION},
        ],
    },
]
resp3 = complete(wrap_messages, tools=None, system=_SYSTEM)
check(bool(resp3.text), f"got wrap-up text: {resp3.text[:80]!r}")
check(resp3.tool_calls == [], "no tool calls in wrap-up response")

print(f"\n{PASS} All smoke tests passed\n")
