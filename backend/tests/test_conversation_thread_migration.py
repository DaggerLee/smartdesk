from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from main import migrate_schema


def _old_schema_engine(path: Path):
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY,
            kb_id INTEGER NOT NULL,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            created_at DATETIME
        )
        """
    )
    connection.commit()
    connection.close()
    return create_engine(f"sqlite:///{path}")


def test_old_conversation_schema_adds_nullable_thread_and_partial_unique_index(
    tmp_path: Path,
) -> None:
    engine = _old_schema_engine(tmp_path / "old.db")

    migrate_schema(engine)
    migrate_schema(engine)

    with engine.connect() as connection:
        columns = {
            row.name: row
            for row in connection.execute(text("PRAGMA table_info(conversations)"))
        }
        index_sql = connection.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'index' AND name = 'ix_conversations_thread_id_unique'"
            )
        ).scalar_one()

    assert columns["thread_id"].notnull == 0
    assert "UNIQUE" in index_sql.upper()
    assert "WHERE thread_id IS NOT NULL" in index_sql


def test_partial_index_allows_legacy_null_rows_but_rejects_duplicate_thread_ids(
    tmp_path: Path,
) -> None:
    engine = _old_schema_engine(tmp_path / "unique.db")
    migrate_schema(engine)

    insert = text(
        "INSERT INTO conversations "
        "(kb_id, question, answer, thread_id) "
        "VALUES (:kb_id, :question, :answer, :thread_id)"
    )
    with engine.begin() as connection:
        connection.execute(
            insert,
            [
                {"kb_id": 1, "question": "legacy one", "answer": "a", "thread_id": None},
                {"kb_id": 1, "question": "legacy two", "answer": "b", "thread_id": None},
                {"kb_id": 1, "question": "new", "answer": "c", "thread_id": "thread-1"},
            ],
        )

    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                insert,
                {"kb_id": 1, "question": "duplicate", "answer": "d", "thread_id": "thread-1"},
            )


def test_migration_does_not_hide_unique_index_creation_errors(tmp_path: Path) -> None:
    engine = _old_schema_engine(tmp_path / "invalid-existing-data.db")
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE conversations ADD COLUMN thread_id VARCHAR(64)"))
        connection.execute(
            text(
                "INSERT INTO conversations (kb_id, question, answer, thread_id) VALUES "
                "(1, 'one', 'a', 'duplicate'), (1, 'two', 'b', 'duplicate')"
            )
        )

    with pytest.raises(IntegrityError):
        migrate_schema(engine)
