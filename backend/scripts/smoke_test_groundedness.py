#!/usr/bin/env python3
"""Smoke test for agent/groundedness.py against the real Gemini API.

Scenarios:
  1. Grounded answer — all claims present in evidence → supported=True
  2. Ungrounded answer — invented facts, unrelated evidence → supported=False
     with specific unsupported sentences identified
  3. Empty evidence — fast-path returns supported=True without an API call

Run from backend/:
    python3 scripts/smoke_test_groundedness.py
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

from agent.groundedness import check

PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"


def ok(cond: bool, msg: str) -> None:
    print(f"  {PASS if cond else FAIL} {msg}")
    if not cond:
        sys.exit(1)


# ── 1. Grounded answer ─────────────────────────────────────────────────────────
print("\n[1] Grounded answer — all claims supported by evidence")

evidence_1 = [
    {"text": "ChromaDB is a vector database that stores embeddings locally.", "source": "docs.pdf"},
    {"text": "Cosine distance below 0.8 is considered relevant in this system.", "source": "config.py"},
]
answer_1 = "ChromaDB stores embeddings locally and relevance is determined by cosine distance."

result_1 = check(answer_1, evidence_1)
print(f"    supported={result_1['supported']}, unsupported={result_1['unsupported_sentences']}")
ok(result_1["supported"] is True, "supported=True for grounded answer")
ok(result_1["unsupported_sentences"] == [], "no unsupported sentences")


# ── 2. Ungrounded answer ───────────────────────────────────────────────────────
print("\n[2] Ungrounded answer — invented facts + unrelated evidence")

evidence_2 = [
    {"text": "FastAPI is a modern Python web framework based on standard Python type hints.", "source": "readme.md"},
]
answer_2 = (
    "The system uses GPT-4 Turbo for embeddings, trained on 2 trillion tokens. "
    "It achieves 99.7% accuracy on the MMLU benchmark. "
    "FastAPI is a Python web framework."   # last sentence IS in evidence
)

result_2 = check(answer_2, evidence_2)
print(f"    supported={result_2['supported']}")
print("    unsupported_sentences:")
for s in result_2["unsupported_sentences"]:
    print(f"      - {s}")
ok(result_2["supported"] is False, "supported=False for invented facts")
ok(len(result_2["unsupported_sentences"]) >= 1, "at least one unsupported sentence identified")


# ── 3. Empty evidence fast-path ────────────────────────────────────────────────
print("\n[3] Empty evidence → fast-path supported=True (no API call)")

result_3 = check("Any answer here.", [])
ok(result_3["supported"] is True, "empty evidence → supported=True")
ok(result_3["unsupported_sentences"] == [], "no unsupported sentences")


# ── Show last trace entries ────────────────────────────────────────────────────
print("\n[Trace] Last groundedness_judge entries from JSONL:")
import json as _json

trace_path = Path(config.TRACE_LOG_PATH)
if trace_path.exists():
    lines = trace_path.read_text().splitlines()
    judge_lines = [l for l in lines if '"groundedness_judge"' in l][-3:]
    for line in judge_lines:
        print(f"  {line}")
else:
    print("  (trace file not found — run the app first to create it)")

print(f"\n{PASS} All groundedness smoke tests passed\n")
