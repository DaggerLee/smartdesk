#!/usr/bin/env python3
"""backend/eval/run_eval.py — SmartDesk v2 W3 baseline eval harness.

Three-layer metrics
-------------------
Layer 1  Router accuracy
    strict  — all 35 items
    clean   — excluding category=boundary or "边界" in notes

Layer 2  Retrieval recall@k
    eligible — expected_route in (rag, agent) AND category != unanswerable
    hit      — ≥1 keyword from expected_answer_contains found in top-k chunks

Layer 3  E2E answer quality
    contains_pass      — answer hits ≥ min_hits from expected_answer_contains
    grounded_rate      — grounding_required=True items passing groundedness judge
    faithfulness       — RAGAS-inspired LLM-as-judge; agent-expected items only
    answer_relevancy   — RAGAS-inspired LLM-as-judge; agent-expected items only

Pipeline mapping (eval uses v2 for all non-direct routes)
    direct  → complete() with no tools
    rag     → RetrieveTool + complete() with context
    agent   → run_agent() full loop

Usage
-----
    cd backend
    python3 eval/run_eval.py                          # full 35-item run
    python3 eval/run_eval.py --limit 5                # smoke test
    python3 eval/run_eval.py --out results_v2.jsonl   # custom output path
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

_BACKEND = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(_BACKEND))
os.chdir(_BACKEND)

from dotenv import load_dotenv
load_dotenv()

# Eval traffic isolation: use the dedicated eval API key when present, so eval
# runs don't consume the dev key's daily quota. Must run before importing
# config (which reads GEMINI_API_KEY at import time).
_eval_key = os.getenv("GEMINI_API_KEY_EVAL")
if _eval_key:
    os.environ["GEMINI_API_KEY"] = _eval_key
    print("[run_eval] Using GEMINI_API_KEY_EVAL for this run")
else:
    print("[run_eval] GEMINI_API_KEY_EVAL not set — falling back to GEMINI_API_KEY")

# Throttle every LLM call (router/judge/generate/groundedness) to stay under
# the free-tier RPM limit; see llm/client._throttle. Overridable from the shell.
os.environ.setdefault("LLM_MIN_INTERVAL_S", "6")
print(f"[run_eval] LLM_MIN_INTERVAL_S={os.environ['LLM_MIN_INTERVAL_S']}")

import config
from agent.groundedness import check as _groundedness_check
from llm.trace import context as _trace_context
from agent.loop import run_agent
from agent.router import route as _router_route
from agent.tools.retrieve import RetrieveTool
from llm.client import complete

# ── Constants ──────────────────────────────────────────────────────────────────

GOLD_PATH = Path(__file__).parent / "gold_set.jsonl"
DEFAULT_OUT = Path(__file__).parent / "results_baseline.jsonl"
TOP_K = 5

# Phrases that count as "this content came from web search, not the KB" for
# the unanswerable-category source-disclosure check (see _has_source_disclosure).
_SOURCE_DISCLOSURE_PHRASES = [
    "网络搜索", "web search", "外部搜索", "互联网", "在线搜索", "网上搜索",
    "非知识库", "不在知识库", "知识库中没有", "知识库中并未", "知识库未提及",
    "来自网络", "并非来自知识库", "基于网络", "根据搜索结果", "外部来源",
]


# ── Keyword matching normalization ──────────────────────────────────────────

def _normalize(text: str) -> str:
    """NFKC (full-width→half-width) + ×→x + casefold, for keyword matching."""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("×", "x")
    return text.lower()


def _keyword_hit(keyword: str, haystack_norm: str) -> bool:
    """A keyword may be a '|'-separated synonym group; any variant matching counts."""
    return any(_normalize(v) in haystack_norm for v in keyword.split("|"))


def _has_source_disclosure(answer: str) -> bool:
    norm = _normalize(answer)
    return any(_normalize(p) in norm for p in _SOURCE_DISCLOSURE_PHRASES)

_FAITH_SYSTEM = (
    "You are a strict RAG faithfulness judge. "
    "Only mark a claim as supported if it is directly inferable from the "
    "provided context — not from general background knowledge."
)
_RELEV_SYSTEM = (
    "You are a question-answering relevancy judge. "
    "Rate only how well the answer addresses the specific question asked."
)
_RAG_PROMPT_TMPL = (
    "Use the following knowledge base excerpts to answer the question.\n"
    "If the excerpts do not contain relevant information, say so clearly.\n\n"
    "Knowledge base:\n{context}\n\n"
    "Question: {query}"
)


# ── Data model ─────────────────────────────────────────────────────────────────

@dataclass
class ItemResult:
    id: str
    query: str
    category: str
    difficulty: str
    expected_route: str
    actual_route: str
    route_correct: bool
    is_boundary: bool

    retrieval_hit: Optional[bool] = None
    retrieval_keyword_hits: int = 0
    relevance_ok: Optional[bool] = None

    answer: str = ""
    contains_hits: int = 0
    contains_pass: bool = False

    grounded: Optional[bool] = None
    faithfulness: Optional[float] = None
    answer_relevancy: Optional[float] = None

    # unanswerable-category only: None = honest refusal (no check needed),
    # True/False = substantive content was given and did/didn't disclose
    # that it came from web search rather than the KB.
    source_disclosed: Optional[bool] = None

    latency_s: float = 0.0
    error: Optional[str] = None


# ── Gold set loader ────────────────────────────────────────────────────────────

def _load_gold(limit: Optional[int] = None) -> list[dict]:
    items = []
    with open(GOLD_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items[:limit] if limit else items


# ── LLM judge helpers ──────────────────────────────────────────────────────────

def _parse_judge_json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*\n?", "", (text or "").strip())
    text = re.sub(r"\n?```\s*$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*?\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
    return {"score": 0.5, "parse_error": True}


def _faithfulness(answer: str, chunks: list[str]) -> float:
    if not chunks or not answer.strip():
        return 0.0
    context = "\n\n".join(f"[Chunk {i+1}]\n{c}" for i, c in enumerate(chunks))
    prompt = (
        f"Context:\n{context}\n\n"
        f"Answer:\n{answer}\n\n"
        "What fraction (0.0-1.0) of the factual claims in the Answer are "
        "directly supported by the Context? "
        'Output JSON only: {"score": <float 0-1>, "reasoning": "<one sentence>"}'
    )
    resp = complete(
        messages=[{"role": "user", "parts": [{"text": prompt}]}],
        tools=None,
        system=_FAITH_SYSTEM,
        temperature=0,
    )
    return min(1.0, max(0.0, float(_parse_judge_json(resp.text or "").get("score", 0.5))))


def _answer_relevancy(query: str, answer: str) -> float:
    if not answer.strip():
        return 0.0
    prompt = (
        f"Question: {query}\n\n"
        f"Answer:\n{answer}\n\n"
        "Rate how well this Answer addresses the Question "
        "(0.0 = completely off-topic, 1.0 = fully addresses all aspects). "
        'Output JSON only: {"score": <float 0-1>, "reasoning": "<one sentence>"}'
    )
    resp = complete(
        messages=[{"role": "user", "parts": [{"text": prompt}]}],
        tools=None,
        system=_RELEV_SYSTEM,
        temperature=0,
    )
    return min(1.0, max(0.0, float(_parse_judge_json(resp.text or "").get("score", 0.5))))


# ── Pipeline runners ───────────────────────────────────────────────────────────

def _run_direct(query: str) -> str:
    resp = complete(
        messages=[{"role": "user", "parts": [{"text": query}]}],
        tools=None,
    )
    return resp.text or ""


def _run_rag(query: str, chunks: list[str]) -> str:
    context = (
        "\n\n".join(f"[Chunk {i+1}]\n{c}" for i, c in enumerate(chunks))
        if chunks else "(No relevant documents found in the knowledge base.)"
    )
    resp = complete(
        messages=[{"role": "user", "parts": [{"text": _RAG_PROMPT_TMPL.format(
            context=context, query=query,
        )}]}],
        tools=None,
    )
    return resp.text or ""


def _run_agent_path(query: str, kb_id: int) -> tuple[str, list[str], Optional[bool]]:
    events = list(run_agent(query, kb_id=kb_id))
    answer = ""
    grounded: Optional[bool] = None
    evidence: list[dict] = []
    for ev in events:
        if ev.type == "final":
            answer = ev.data.get("text", "") or ""
            grounded = ev.data.get("grounded")
            evidence = ev.data.get("evidence", [])
    chunks = [e["text"] for e in evidence if isinstance(e, dict) and "text" in e]
    return answer, chunks, grounded


# ── Single-item eval ───────────────────────────────────────────────────────────

def eval_item(item: dict) -> ItemResult:
    with _trace_context(item_id=item["id"]):
        return _eval_item(item)


def _eval_item(item: dict) -> ItemResult:
    t0 = time.time()
    is_boundary = (
        item.get("category") == "boundary"
        or "边界" in item.get("notes", "")
    )

    result = ItemResult(
        id=item["id"],
        query=item["query"],
        category=item["category"],
        difficulty=item.get("difficulty", ""),
        expected_route=item["expected_route"],
        actual_route="",
        route_correct=False,
        is_boundary=is_boundary,
    )

    try:
        actual_route = _router_route(item["query"])
        result.actual_route = actual_route
        result.route_correct = actual_route == item["expected_route"]

        keywords = item.get("expected_answer_contains", [])
        min_hits = item.get("min_hits", 1)

        # Layer 2 — retrieval recall@k (independent of pipeline runner)
        retrieved_chunks: list[str] = []
        do_retrieval = (
            item["expected_route"] in ("rag", "agent")
            and item["category"] != "unanswerable"
        )
        if do_retrieval:
            r = RetrieveTool(kb_id=item["kb_id"]).run(query=item["query"])
            retrieved_chunks = r.get("chunks", [])
            result.relevance_ok = r.get("relevance_ok", False)
            chunks_norm = _normalize(" ".join(retrieved_chunks))
            kw_hits = sum(1 for kw in keywords if _keyword_hit(kw, chunks_norm))
            result.retrieval_keyword_hits = kw_hits
            result.retrieval_hit = kw_hits >= 1

        # Layer 3a — generate answer via actual route
        answer = ""
        grounded: Optional[bool] = None

        if actual_route == "direct":
            answer = _run_direct(item["query"])
        elif actual_route == "rag":
            answer = _run_rag(item["query"], retrieved_chunks)
        else:  # agent
            answer, agent_chunks, grounded = _run_agent_path(item["query"], item["kb_id"])
            if agent_chunks:
                retrieved_chunks = agent_chunks

        result.answer = answer

        # Layer 3b — contains check
        answer_norm = _normalize(answer)
        hits = sum(1 for kw in keywords if _keyword_hit(kw, answer_norm))
        result.contains_hits = hits
        result.contains_pass = hits >= min_hits

        # Layer 3b.2 — unanswerable-category source disclosure (only when the
        # model gave substantive content instead of an honest refusal).
        if item["category"] == "unanswerable" and not result.contains_pass and answer:
            result.source_disclosed = _has_source_disclosure(answer)

        # Layer 3c — groundedness
        if item.get("grounding_required", False) and answer:
            if grounded is None:
                evidence = [{"text": c, "source": "retrieved"} for c in retrieved_chunks]
                g = _groundedness_check(answer, evidence)
                grounded = g.get("supported", True)
            result.grounded = grounded

        # Layer 3d — RAGAS-inspired (agent-expected only, per spec)
        if item["expected_route"] == "agent" and answer:
            result.faithfulness = _faithfulness(answer, retrieved_chunks)
            result.answer_relevancy = _answer_relevancy(item["query"], answer)

    except Exception as exc:
        result.error = str(exc)

    result.latency_s = round(time.time() - t0, 2)
    return result


# ── Aggregation ────────────────────────────────────────────────────────────────

def _pct(n: int, d: int) -> str:
    return f"{n}/{d} = {100*n/d:.1f}%" if d else "N/A"


def _mean(vals: list[float]) -> Optional[float]:
    return round(sum(vals) / len(vals), 3) if vals else None


def aggregate(results: list[ItemResult]) -> dict:
    total = len(results)
    clean = [r for r in results if not r.is_boundary]
    retrieval_eligible = [r for r in results if r.retrieval_hit is not None]
    grounded_eligible = [r for r in results if r.grounded is not None]
    faith_vals = [r.faithfulness for r in results if r.faithfulness is not None]
    relev_vals = [r.answer_relevancy for r in results if r.answer_relevancy is not None]
    source_checked = [r for r in results if r.source_disclosed is not None]

    return {
        "router_accuracy_strict":  _pct(sum(1 for r in results if r.route_correct), total),
        "router_accuracy_clean":   _pct(sum(1 for r in clean if r.route_correct), len(clean)),
        "boundary_excluded":       total - len(clean),
        "retrieval_recall_k":      _pct(sum(1 for r in retrieval_eligible if r.retrieval_hit), len(retrieval_eligible)),
        "relevance_ok_rate":       _pct(sum(1 for r in retrieval_eligible if r.relevance_ok), len(retrieval_eligible)),
        "e2e_contains_pass":       _pct(sum(1 for r in results if r.contains_pass), total),
        "grounded_rate":           _pct(sum(1 for r in grounded_eligible if r.grounded), len(grounded_eligible)),
        "faithfulness_mean":       _mean(faith_vals),
        "answer_relevancy_mean":   _mean(relev_vals),
        "faithfulness_n":          len(faith_vals),
        "answer_relevancy_n":      len(relev_vals),
        "u_source_disclosure_rate": _pct(sum(1 for r in source_checked if r.source_disclosed), len(source_checked)),
        "u_source_disclosure_n":  len(source_checked),
        "total":                   total,
        "errors":                  sum(1 for r in results if r.error),
    }


# ── Report ─────────────────────────────────────────────────────────────────────

def print_report(agg: dict, results: list[ItemResult]) -> None:
    run_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    w = 66
    print(f"\n{'='*w}")
    print(f"  SmartDesk v2 Baseline Eval  —  {run_at}")
    print(f"  embedding: all-MiniLM-L6-v2  (before multilingual swap)")
    print(f"{'='*w}")

    print(f"\n[Layer 1] Router Accuracy")
    print(f"  strict ({agg['total']} items):   {agg['router_accuracy_strict']}")
    print(f"  clean  ({agg['total']-agg['boundary_excluded']} items):   {agg['router_accuracy_clean']}")
    print(f"  [{agg['boundary_excluded']} boundary item(s) excluded from clean]")

    print(f"\n[Layer 2] Retrieval Recall@{TOP_K}")
    print(f"  keyword hit rate:   {agg['retrieval_recall_k']}")
    print(f"  relevance_ok rate:  {agg['relevance_ok_rate']}")
    print(f"  [relevance_ok expected ~0%: Chinese text vs English MiniLM]")

    print(f"\n[Layer 3] E2E Answer Quality")
    print(f"  contains_pass:      {agg['e2e_contains_pass']}")
    print(f"  grounded_rate:      {agg['grounded_rate']}")
    print(f"  u_source_disclosure: {agg['u_source_disclosure_rate']}  (n={agg['u_source_disclosure_n']}, only items with substantive content)")

    print(f"\n[Layer 3] RAGAS-inspired  (agent-expected, n={agg['faithfulness_n']})")
    print(f"  faithfulness:       {agg['faithfulness_mean']}")
    print(f"  answer_relevancy:   {agg['answer_relevancy_mean']}")

    if agg["errors"]:
        print(f"\n  ⚠  {agg['errors']} item(s) had errors")

    hdr = f"{'ID':<6} {'Category':<14} {'Dif':<6} {'Exp':<7} {'Act':<7} {'Rt':<3} {'Ret':<4} {'Con':<4} {'Gnd':<4} {'Fth':<5} {'Rel':<5}  {'ms':>6}"
    sep = "─" * len(hdr)
    print(f"\n{sep}")
    print(hdr)
    print(sep)
    for r in results:
        rt  = "✓" if r.route_correct else "✗"
        ret = ("✓" if r.retrieval_hit else "✗") if r.retrieval_hit is not None else " -"
        con = "✓" if r.contains_pass else "✗"
        gnd = ("✓" if r.grounded else "✗") if r.grounded is not None else " -"
        fth = f"{r.faithfulness:.2f}" if r.faithfulness is not None else "  -  "
        rel = f"{r.answer_relevancy:.2f}" if r.answer_relevancy is not None else "  -  "
        ms  = int(r.latency_s * 1000)
        err = " ERR" if r.error else ""
        print(
            f"{r.id:<6} {r.category:<14} {r.difficulty:<6} "
            f"{r.expected_route:<7} {r.actual_route:<7} "
            f"{rt:<3} {ret:<4} {con:<4} {gnd:<4} {fth:<5} {rel:<5}  {ms:>6}{err}"
        )
    print(sep)


# ── Run history archive ────────────────────────────────────────────────────────

HISTORY_PATH = Path(__file__).parent / "results" / "history.jsonl"
LOCK_PATH = HISTORY_PATH.parent / ".lock"


def _acquire_lock() -> None:
    """Refuse to start while another eval run is alive.

    The lock file holds the owning PID. Two concurrent runs hammer the same
    API quota and poison each other's results, so this is enforced by code
    rather than by remembering to kill old processes. A lock left behind by
    a dead process is treated as stale and taken over.
    """
    if LOCK_PATH.exists():
        try:
            other_pid = int(LOCK_PATH.read_text().strip())
        except ValueError:
            other_pid = None
        if other_pid is not None:
            try:
                os.kill(other_pid, 0)
            except ProcessLookupError:
                print(f"[run_eval] Stale lock from dead PID {other_pid} — taking over", flush=True)
            else:
                sys.exit(
                    f"[run_eval] Another eval run is active (PID {other_pid}). "
                    f"Refusing to start. If that's wrong, remove {LOCK_PATH}."
                )
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.write_text(str(os.getpid()))


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
    except Exception:
        return "unknown"


def _git_dirty() -> bool:
    """True if the working tree has uncommitted changes (staged or not).

    An eval archived against a dirty tree records a git_commit that doesn't
    match the code that actually ran — see the 20260712_fixw4 incident.
    """
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout
        return bool(out.strip())
    except Exception:
        # Can't verify cleanliness (e.g. not a git repo) — treat as dirty
        # rather than silently vouching for a state we couldn't check.
        return True


def append_history(agg: dict, n_items: int, limit: Optional[int], git_dirty: bool) -> None:
    """Append one aggregate record per eval run — the before/after comparison
    data source (Decisions §3: archive every run, keep the file in git)."""
    record = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "git_commit": _git_commit(),
        "model": config.GEMINI_MODEL,
        "gold_set_items": n_items,
        "limit": limit,
        "eval_key_used": bool(_eval_key),
        **agg,
    }
    if git_dirty:
        record["git_dirty"] = True
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_PATH, "a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"Run archived → {HISTORY_PATH}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="SmartDesk v2 eval harness")
    parser.add_argument("--limit", type=int, default=None,
                        help="Evaluate only first N items (smoke test)")
    parser.add_argument("--out", default=str(DEFAULT_OUT),
                        help="Output JSONL path for detailed per-item results")
    parser.add_argument("--run-id", default=None,
                        help="Resume ID; defaults to <date>_<commit>. Items already "
                             "in partial_<run_id>.jsonl are skipped on restart.")
    parser.add_argument("--allow-dirty", action="store_true",
                        help="Run even with uncommitted changes in the working tree. "
                             "The archived history.jsonl record is forced to include "
                             "\"git_dirty\": true so the mismatch is never silent.")
    args = parser.parse_args()

    dirty = _git_dirty()
    if dirty and not args.allow_dirty:
        sys.exit(
            "[run_eval] Working tree has uncommitted changes — refusing to run "
            "(the archived record's git_commit would not match the code that "
            "actually ran). Commit first, or pass --allow-dirty to override."
        )

    _acquire_lock()
    try:
        _run(args, git_dirty=dirty)
    finally:
        LOCK_PATH.unlink(missing_ok=True)


def _run(args: argparse.Namespace, git_dirty: bool) -> None:
    items = _load_gold(args.limit)
    print(f"Evaluating {len(items)} items …", flush=True)

    # Checkpoint file: one line per completed item, written immediately, so a
    # mid-run crash never throws away finished work.
    run_id = args.run_id or f"{datetime.now():%Y%m%d}_{_git_commit()}"
    partial_path = HISTORY_PATH.parent / f"partial_{run_id}.jsonl"
    done: dict[str, ItemResult] = {}
    if partial_path.exists():
        with open(partial_path) as f:
            for line in f:
                rec = json.loads(line)
                if rec.get("error"):
                    continue  # errored items are re-run, not treated as done
                done[rec["id"]] = ItemResult(**rec)
        print(f"[run_eval] Resuming run {run_id}: {len(done)} item(s) already done", flush=True)
    partial_path.parent.mkdir(parents=True, exist_ok=True)

    results: list[ItemResult] = []
    for i, item in enumerate(items, 1):
        if item["id"] in done:
            results.append(done[item["id"]])
            print(f"  [{i:>2}/{len(items)}] {item['id']:<6} ({item['category']}) … skipped (resumed)",
                  flush=True)
            continue
        print(f"  [{i:>2}/{len(items)}] {item['id']:<6} ({item['category']}) … ",
              end="", flush=True)
        r = eval_item(item)
        results.append(r)
        with open(partial_path, "a") as pf:
            pf.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
        if r.error:
            print(f"ERR  {r.latency_s:.1f}s  {r.error[:60]}", flush=True)
        else:
            status = "✓" if (r.route_correct and r.contains_pass) else "~"
            print(f"{status}    {r.latency_s:.1f}s", flush=True)

    agg = aggregate(results)
    print_report(agg, results)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        for r in results:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
    print(f"\nDetailed results → {out_path}")

    append_history(agg, n_items=len(items), limit=args.limit, git_dirty=git_dirty)
    print()


if __name__ == "__main__":
    main()
