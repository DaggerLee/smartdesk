from __future__ import annotations

import json
import sqlite3

from scripts.shadow_write_intent import load_conversation_rows, load_eval_rows


def test_load_eval_rows_reads_queries_without_mutating_file(tmp_path):
    path = tmp_path / "gold.jsonl"
    original = (
        json.dumps({"id": "g1", "query": "Save this as a note file."})
        + "\n"
        + json.dumps({"id": "g2", "query": "What is MCP?"})
        + "\n"
    )
    path.write_text(original, encoding="utf-8")

    rows = load_eval_rows(path)

    assert rows == [
        {"source": "eval", "id": "g1", "text": "Save this as a note file."},
        {"source": "eval", "id": "g2", "text": "What is MCP?"},
    ]
    assert path.read_text(encoding="utf-8") == original


def test_load_conversation_rows_opens_database_read_only(tmp_path):
    path = tmp_path / "smartdesk.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE conversations (id INTEGER PRIMARY KEY, question TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO conversations(question) VALUES (?)", ("记录一下",))
        conn.commit()
    before = path.read_bytes()

    rows = load_conversation_rows(path)

    assert rows == [{"source": "conversation", "id": "1", "text": "记录一下"}]
    assert path.read_bytes() == before
