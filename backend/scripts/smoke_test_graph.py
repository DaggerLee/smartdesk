#!/usr/bin/env python3
"""Smoke test for agent/graph.py against the real Gemini API.

Runs one query down each of the three routing paths (direct / rag / agent)
through the LangGraph skeleton and prints (a) the router's decision and (b)
the resulting answer, so the output can be eyeballed against the equivalent
call made through the legacy path (route()/ llm_stream() / run_agent() /
chat.py's inline RAG chain directly).

Note: LLM output is non-deterministic (no fixed seed, no temperature=0 on the
final-answer calls), so this does NOT assert byte-identical text between the
graph and legacy paths — only that (1) the router picks the same path for a
query worded to be unambiguous for that path, and (2) the graph path completes
without error and returns a non-empty, on-topic answer.

Run from backend/:
    python3 scripts/smoke_test_graph.py

Set SMOKE_INCLUDE_LEGACY=1 to also run the legacy route()/run_agent() side by
side for comparison (roughly doubles the API calls — legacy's agent path alone
is 1 router call + N tool-round calls + a groundedness judge call). Off by
default: the legacy path was already verified against the graph path in
T1/T2, and this script's job going forward is to smoke-test agent/graph.py
itself, not to re-prove legacy still works.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

import config

if not config.GEMINI_API_KEY:
    print("✗ GEMINI_API_KEY not set — copy .env.example to .env and fill it in")
    sys.exit(1)

INCLUDE_LEGACY = os.getenv("SMOKE_INCLUDE_LEGACY") == "1"

from agent.graph import run_graph
from agent.loop import run_agent
from agent.router import route
from llm.client import stream as llm_stream

KB_ID = 1  # "Agentic AI Notes" — existing local KB, see database.py

PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"


def check(cond: bool, msg: str) -> None:
    print(f"  {PASS if cond else FAIL} {msg}")
    if not cond:
        sys.exit(1)


# ── 1. direct path ────────────────────────────────────────────────────────────
# Skipped on this run — already verified real-API on 2026-07-18 (legacy and
# graph sides matched). Per user instruction, today's quota-reset rerun only
# re-verifies the agent path, which was blocked by RPD exhaustion yesterday.
#
# print("\n[1] direct path — 'Hi, who are you?'")
# query = "Hi, who are you?"
#
# legacy_route = route(query)
# check(legacy_route == "direct", f"legacy router picked 'direct', got {legacy_route!r}")
# legacy_answer = "".join(llm_stream([{"role": "user", "parts": [{"text": query}]}]))
# print(f"    legacy answer: {legacy_answer[:100]!r}")
#
# result = run_graph(query, KB_ID)
# check(result["route"] == "direct", f"graph picked 'direct', got {result['route']!r}")
# check(bool(result.get("answer")), f"graph answer non-empty: {result.get('answer', '')[:100]!r}")


# ── 2. rag path ───────────────────────────────────────────────────────────────
# Skipped on this run — same reason as [1] above.
#
# print("\n[2] rag path — factual question the KB should answer")
# query = "What is the difficulty spectrum of agentic applications?"
#
# legacy_route = route(query)
# check(legacy_route == "rag", f"legacy router picked 'rag', got {legacy_route!r}")
#
# result = run_graph(query, KB_ID)
# check(result["route"] == "rag", f"graph picked 'rag', got {result['route']!r}")
# check(bool(result.get("answer")), f"graph answer non-empty: {result.get('answer', '')[:100]!r}")
# check("context_texts" in result, "graph state carries context_texts (rag-path field)")
# print(f"    graph answer: {result.get('answer', '')[:150]!r}")
# print(f"    doc_sources: {[s['filename'] for s in result.get('doc_sources', [])]}")


# ── 3. agent path ─────────────────────────────────────────────────────────────
print("\n[3] agent path — comparison requiring multiple retrievals")
query = "Compare LoRA and QLoRA, and explain when each should be used."

if INCLUDE_LEGACY:
    legacy_route = route(query)
    check(legacy_route == "agent", f"legacy router picked 'agent', got {legacy_route!r}")

    final_data = None
    for event in run_agent(query, KB_ID):
        if event.type == "tool_call":
            print(f"    legacy tool_call: {event.data['name']}({event.data['args']})")
        elif event.type == "final":
            final_data = event.data
    check(final_data is not None, "legacy run_agent() produced a final event")
    print(f"    legacy answer: {final_data['text'][:150]!r}")
else:
    print("    (legacy comparison skipped — SMOKE_INCLUDE_LEGACY=1 to include it)")

result = run_graph(query, KB_ID)
check(result["route"] == "agent", f"graph picked 'agent', got {result['route']!r}")
check(bool(result.get("answer")), f"graph answer non-empty: {result.get('answer', '')[:150]!r}")
check("evidence" in result and len(result["evidence"]) > 0, "graph state carries non-empty evidence (agent-path field)")
print(f"    graph answer: {result.get('answer', '')[:150]!r}")

print(f"\n{PASS} All graph smoke tests passed\n")
