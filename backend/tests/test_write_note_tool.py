from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent.tools.write_note import WriteNoteTool


def test_write_note_creates_canonical_markdown_in_user_directory(tmp_path: Path) -> None:
    receipt = WriteNoteTool(user_id=7, action_id="act-123", notes_root=tmp_path).run(
        title="Meeting Notes",
        content="  Keep leading whitespace.\n",
    )

    expected = b"# Meeting Notes\n\n  Keep leading whitespace.\n\n"
    target = tmp_path / "users" / "7" / "notes" / "meeting-notes-act-123.md"
    assert target.read_bytes() == expected
    assert receipt.result == "succeeded"
    assert receipt.relative_path == "notes/meeting-notes-act-123.md"
    assert receipt.byte_count == len(expected)
    assert receipt.read_back_verified is True


def test_write_note_isolated_by_authenticated_user_id(tmp_path: Path) -> None:
    first = WriteNoteTool(user_id=7, action_id="act-1", notes_root=tmp_path).run("Title", "one")
    second = WriteNoteTool(user_id=8, action_id="act-1", notes_root=tmp_path).run("Title", "two")

    assert first.relative_path == second.relative_path == "notes/title-act-1.md"
    assert (tmp_path / "users" / "7" / "notes" / "title-act-1.md").read_text() == "# Title\n\none\n"
    assert (tmp_path / "users" / "8" / "notes" / "title-act-1.md").read_text() == "# Title\n\ntwo\n"


def test_identical_existing_file_is_replayed_without_overwrite(tmp_path: Path) -> None:
    tool = WriteNoteTool(user_id=7, action_id="act-1", notes_root=tmp_path)
    first = tool.run("Title", "body")
    target = tmp_path / "users" / "7" / first.relative_path
    before = target.stat().st_ino

    replay = tool.run("Title", "body")

    assert replay.result == "replayed"
    assert replay.read_back_verified is True
    assert target.stat().st_ino == before


def test_different_existing_file_returns_conflict_without_overwrite(tmp_path: Path) -> None:
    tool = WriteNoteTool(user_id=7, action_id="act-1", notes_root=tmp_path)
    receipt = tool.run("Title", "original")
    target = tmp_path / "users" / "7" / receipt.relative_path

    conflict = tool.run("Title", "different")

    assert conflict.result == "conflict"
    assert target.read_text() == "# Title\n\noriginal\n"


def test_constructor_and_reject_path_have_no_filesystem_side_effect(tmp_path: Path) -> None:
    WriteNoteTool(user_id=7, action_id="act-1", notes_root=tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("action_id", ["../escape", "a/b", "a\\b", "", ".", ".."])
def test_action_id_cannot_control_the_path(tmp_path: Path, action_id: str) -> None:
    with pytest.raises(ValueError):
        WriteNoteTool(user_id=7, action_id=action_id, notes_root=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_title_path_separators_are_rejected_before_directory_creation(tmp_path: Path) -> None:
    tool = WriteNoteTool(user_id=7, action_id="act-1", notes_root=tmp_path)
    with pytest.raises(ValueError):
        tool.run("unsafe/title", "body")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(not hasattr(Path, "symlink_to"), reason="symlinks unavailable")
def test_users_parent_symlink_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (tmp_path / "users").symlink_to(outside, target_is_directory=True)

    receipt = WriteNoteTool(user_id=7, action_id="act-1", notes_root=tmp_path).run("Title", "body")

    assert receipt.result == "failed"
    assert receipt.error_code == "unsafe_path"
    assert list(outside.iterdir()) == []


@pytest.mark.skipif(not hasattr(Path, "symlink_to"), reason="symlinks unavailable")
def test_final_target_symlink_is_never_followed_or_overwritten(tmp_path: Path) -> None:
    notes = tmp_path / "users" / "7" / "notes"
    notes.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("outside")
    (notes / "title-act-1.md").symlink_to(outside)

    receipt = WriteNoteTool(user_id=7, action_id="act-1", notes_root=tmp_path).run("Title", "body")

    assert receipt.result == "conflict"
    assert outside.read_text() == "outside"


def test_concurrent_identical_calls_publish_once_then_replay(tmp_path: Path) -> None:
    def write() -> str:
        return WriteNoteTool(user_id=7, action_id="act-1", notes_root=tmp_path).run(
            "Title", "body"
        ).result

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: write(), range(2)))

    assert sorted(results) == ["replayed", "succeeded"]
    files = list((tmp_path / "users" / "7" / "notes").iterdir())
    assert [path.name for path in files] == ["title-act-1.md"]
