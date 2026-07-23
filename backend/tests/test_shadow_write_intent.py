from __future__ import annotations

from scripts.shadow_write_intent import classify_candidates


def test_shadow_replay_returns_only_draft_and_persist_candidates():
    rows = [
        {"source": "conversation", "id": "1", "text": "What is MCP?"},
        {"source": "conversation", "id": "2", "text": "Save this as a note file."},
        {"source": "eval", "id": "g1", "text": "Draft a note but do not save it."},
    ]

    report = classify_candidates(rows)

    assert report == {
        "candidates": [
            {"source": "conversation", "id": "2", "text": "Save this as a note file.", "intent": "persist"},
            {"source": "eval", "id": "g1", "text": "Draft a note but do not save it.", "intent": "draft"},
        ],
        "candidate_counts": {"persist": 1, "draft": 1},
        "accuracy_metrics": None,
        "accuracy_note": "Human adjudication is required before reporting precision or false-positive rates.",
    }


def test_shadow_replay_does_not_mutate_input_rows():
    rows = [{"source": "eval", "id": "g1", "text": "Save this as a note file."}]
    original = [dict(row) for row in rows]

    classify_candidates(rows)

    assert rows == original
