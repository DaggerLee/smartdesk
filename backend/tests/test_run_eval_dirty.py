"""Tests for run_eval.py's git-dirty guard (Decisions §3: refuse to run on a
dirty working tree by default so archived history.jsonl records always match
the code that actually ran — see the 20260712_fixw4 incident)."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import eval.run_eval as run_eval


def _porcelain_result(output: str):
    class _Result:
        stdout = output
    return _Result()


def test_git_dirty_true_when_status_nonempty():
    with patch("subprocess.run", return_value=_porcelain_result(" M foo.py\n")):
        assert run_eval._git_dirty() is True


def test_git_dirty_false_when_status_empty():
    with patch("subprocess.run", return_value=_porcelain_result("")):
        assert run_eval._git_dirty() is False


def test_git_dirty_true_when_git_unavailable():
    with patch("subprocess.run", side_effect=OSError("git not found")):
        assert run_eval._git_dirty() is True


def test_main_refuses_when_dirty_without_allow_dirty():
    with patch("eval.run_eval._git_dirty", return_value=True), \
         patch("sys.argv", ["run_eval.py"]):
        with pytest.raises(SystemExit):
            run_eval.main()


def test_main_proceeds_when_dirty_with_allow_dirty():
    with patch("eval.run_eval._git_dirty", return_value=True), \
         patch("eval.run_eval._acquire_lock"), \
         patch("eval.run_eval._run") as mock_run, \
         patch("eval.run_eval.LOCK_PATH"), \
         patch("sys.argv", ["run_eval.py", "--allow-dirty"]):
        run_eval.main()
        assert mock_run.call_args.kwargs["git_dirty"] is True


def test_append_history_forces_git_dirty_field(tmp_path):
    with patch.object(run_eval, "HISTORY_PATH", tmp_path / "history.jsonl"), \
         patch("eval.run_eval._git_commit", return_value="abc1234"):
        run_eval.append_history({"total": 1, "errors": 0}, n_items=1, limit=None, git_dirty=True)
        record = json.loads((tmp_path / "history.jsonl").read_text().strip())
        assert record["git_dirty"] is True


def test_append_history_omits_git_dirty_field_when_clean(tmp_path):
    with patch.object(run_eval, "HISTORY_PATH", tmp_path / "history.jsonl"), \
         patch("eval.run_eval._git_commit", return_value="abc1234"):
        run_eval.append_history({"total": 1, "errors": 0}, n_items=1, limit=None, git_dirty=False)
        record = json.loads((tmp_path / "history.jsonl").read_text().strip())
        assert "git_dirty" not in record
