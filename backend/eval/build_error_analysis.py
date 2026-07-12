#!/usr/bin/env python3
"""backend/eval/build_error_analysis.py — build a per-item error-analysis
dossier for failed gold-set items from a completed eval run.

Unlike the ad-hoc W4 dossier (which had to correlate trace entries to items
by cumulative-latency time windowing, because trace lines carried no item
identifier), this script joins on the `item_id` field that eval_item() now
writes into every trace entry via llm.trace.context() — exact, not
approximate. It also stores full evidence text, not a 100-char preview.

Usage (from backend/):
    python3 eval/build_error_analysis.py --run-id 20260711_e8dddab
    python3 eval/build_error_analysis.py --results eval/results_baseline.jsonl \
        --trace logs/traces/traces.jsonl --out eval/results/error_analysis.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).parent


def _load_jsonl(path: Path) -> list[dict]:
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def _failed_item_ids(results: list[dict]) -> list[str]:
    failed = []
    for r in results:
        problems = (
            r.get("error")
            or not r.get("route_correct")
            or not r.get("contains_pass")
            or r.get("grounded") is False
            or r.get("retrieval_hit") is False
        )
        if problems:
            failed.append(r["id"])
    return failed


def build(results_path: Path, trace_path: Path, gold_path: Path) -> dict:
    results = {r["id"]: r for r in _load_jsonl(results_path)}
    gold = {g["id"]: g for g in _load_jsonl(gold_path)}
    trace = _load_jsonl(trace_path) if trace_path.exists() else []

    trace_by_item: dict[str, list[dict]] = {}
    for entry in trace:
        iid = entry.get("item_id")
        if iid:
            trace_by_item.setdefault(iid, []).append(entry)

    dossier: dict[str, dict] = {}
    for iid in _failed_item_ids(list(results.values())):
        g, r = gold.get(iid, {}), results[iid]
        tr = trace_by_item.get(iid, [])
        judges = [e for e in tr if e["type"] in
                  ("groundedness_judge", "groundedness_check", "groundedness_recheck")]
        dossier[iid] = {
            "query": g.get("query"),
            "category": g.get("category"),
            "expected_route": g.get("expected_route"),
            "actual_route": r.get("actual_route"),
            "route_correct": r.get("route_correct"),
            "expected_answer_contains": g.get("expected_answer_contains"),
            "contains_hits": r.get("contains_hits"),
            "contains_pass": r.get("contains_pass"),
            "grounded": r.get("grounded"),
            "faithfulness": r.get("faithfulness"),
            "answer_relevancy": r.get("answer_relevancy"),
            "final_answer_full_text": r.get("answer"),
            "groundedness_judge_events": judges,
            "self_healing_events": {
                "rewrite_hint": [e for e in tr if e["type"] == "rewrite_hint"],
                "tool_error": [e for e in tr if e["type"] == "tool_error"],
                "groundedness_revision_attempted": any(
                    e["type"] == "groundedness_recheck" for e in tr
                ),
            },
            # Full evidence text, not truncated — earlier W4 dossier stored
            # only the first 100 chars per chunk, which wasn't enough to
            # verify judge calls against the actual retrieved passages.
            "raw_trace_entries": tr,
        }
    return dossier


def main() -> None:
    p = argparse.ArgumentParser(description="Build a per-item error-analysis dossier")
    p.add_argument("--run-id", default=None,
                   help="Shortcut: use eval/results_baseline.jsonl + logs/traces/traces.jsonl")
    p.add_argument("--results", default=None, help="Path to results_baseline.jsonl-style file")
    p.add_argument("--trace", default="logs/traces/traces.jsonl")
    p.add_argument("--gold", default=str(_HERE / "gold_set.jsonl"))
    p.add_argument("--out", default=None)
    args = p.parse_args()

    results_path = Path(args.results) if args.results else _HERE / "results_baseline.jsonl"
    trace_path = Path(args.trace)
    gold_path = Path(args.gold)
    out_path = Path(args.out) if args.out else (
        _HERE / "results" / f"error_analysis_{args.run_id}.json" if args.run_id
        else _HERE / "results" / "error_analysis.json"
    )

    dossier = build(results_path, trace_path, gold_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(dossier, f, ensure_ascii=False, indent=2)
    print(f"Dossier written → {out_path} ({len(dossier)} failed item(s))")


if __name__ == "__main__":
    main()
