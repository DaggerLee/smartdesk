from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from agent.write_note_policy import classify_write_intent


def load_eval_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            item = json.loads(line)
            rows.append({"source": "eval", "id": str(item["id"]), "text": item["query"]})
    return rows


def load_conversation_rows(path: Path) -> list[dict]:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        conn.execute("PRAGMA query_only = ON")
        records = conn.execute(
            "SELECT id, question FROM conversations ORDER BY id"
        ).fetchall()
    return [
        {"source": "conversation", "id": str(row_id), "text": question}
        for row_id, question in records
    ]


def classify_candidates(rows: list[dict]) -> dict:
    candidates: list[dict] = []
    counts = {"persist": 0, "draft": 0}
    for row in rows:
        intent = classify_write_intent(row["text"])
        if intent == "none":
            continue
        candidates.append({**row, "intent": intent})
        counts[intent] += 1

    return {
        "candidates": candidates,
        "candidate_counts": counts,
        "accuracy_metrics": None,
        "accuracy_note": (
            "Human adjudication is required before reporting precision or "
            "false-positive rates."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only write-intent shadow replay")
    parser.add_argument("--eval", type=Path, default=Path("eval/gold_set.jsonl"))
    parser.add_argument("--db", type=Path, default=Path("data/smartdesk.db"))
    args = parser.parse_args()

    rows = load_eval_rows(args.eval)
    if args.db.exists():
        rows.extend(load_conversation_rows(args.db))
    print(json.dumps(classify_candidates(rows), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
