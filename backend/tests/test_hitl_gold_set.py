from __future__ import annotations

import json
from pathlib import Path


GOLD_PATH = Path(__file__).parents[1] / "eval" / "hitl_gold_set.jsonl"


def _rows() -> list[dict]:
    return [json.loads(line) for line in GOLD_PATH.read_text().splitlines() if line]


def test_hitl_gold_ids_are_unique_and_kinds_are_known() -> None:
    rows = _rows()
    ids = [row["id"] for row in rows]

    assert len(ids) == len(set(ids))
    assert {row["kind"] for row in rows} == {
        "intent",
        "protocol",
        "receipt",
        "resolution",
    }


def test_hitl_gold_covers_bilingual_persist_draft_and_near_negatives() -> None:
    intents = [row for row in _rows() if row["kind"] == "intent"]

    assert {(row["language"], row["expected_intent"]) for row in intents} >= {
        ("en", "persist"),
        ("zh", "persist"),
        ("en", "draft"),
        ("zh", "draft"),
        ("en", "none"),
        ("zh", "none"),
    }
    near_negative_queries = {
        row["query"]
        for row in intents
        if row["expected_intent"] == "none"
    }
    assert {
        "record this",
        "remember this",
        "note that the deadline is Friday",
        "记录一下",
        "记住这个",
    } <= near_negative_queries


def test_hitl_gold_covers_three_resolutions_and_invalid_write_rounds() -> None:
    rows = _rows()
    resolutions = {
        row["decision"] for row in rows if row["kind"] == "resolution"
    }
    scenarios = {
        row["scenario"] for row in rows if row["kind"] == "protocol"
    }

    assert resolutions == {"approve", "edit", "reject"}
    assert {"mixed_read_write", "multiple_write"} <= scenarios


def test_hitl_gold_covers_all_receipts_and_receipt_only_summarize_save() -> None:
    rows = _rows()
    results = {
        row["result"] for row in rows if row["kind"] == "receipt"
    }
    summarize = next(
        row
        for row in rows
        if row["kind"] == "protocol" and row["scenario"] == "summarize_and_save"
    )

    assert results == {"succeeded", "replayed", "rejected", "conflict", "failed"}
    assert summarize["expected_chat"] == "receipt_only"
    assert summarize["expected_file"] == "generated_summary"
