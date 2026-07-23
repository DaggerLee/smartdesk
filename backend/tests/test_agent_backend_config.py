from pathlib import Path
import os
import subprocess
import sys

import pytest


BACKEND_DIR = Path(__file__).parents[1]
REPO_ROOT = BACKEND_DIR.parent
ENV_VAR = "SMARTDESK_AGENT_BACKEND"


def _import_config(value: str | None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if value is None:
        env.pop(ENV_VAR, None)
    else:
        env[ENV_VAR] = value
    return subprocess.run(
        [sys.executable, "-c", "import config; print(config.AGENT_BACKEND)"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_agent_backend_defaults_to_legacy_when_absent() -> None:
    result = _import_config(None)

    assert result.returncode == 0
    assert result.stdout.strip() == "legacy"


@pytest.mark.parametrize("value", ["legacy", "langgraph"])
def test_agent_backend_accepts_exact_legal_values(value: str) -> None:
    result = _import_config(value)

    assert result.returncode == 0
    assert result.stdout.strip() == value


@pytest.mark.parametrize("value", ["", "Legacy", " langgraph ", "other"])
def test_agent_backend_rejects_every_other_value(value: str) -> None:
    result = _import_config(value)

    assert result.returncode != 0
    assert "SMARTDESK_AGENT_BACKEND must be exactly 'legacy' or 'langgraph'" in result.stderr


def test_chat_and_eval_have_no_independent_backend_environment_reads() -> None:
    chat_source = (BACKEND_DIR / "routers" / "chat.py").read_text()
    eval_source = (BACKEND_DIR / "eval" / "run_eval.py").read_text()

    forbidden = 'os.getenv("SMARTDESK_AGENT_BACKEND"'
    assert forbidden not in chat_source
    assert forbidden not in eval_source
    assert "get_agent_backend()" in chat_source
    assert "_AGENT_BACKEND = config.AGENT_BACKEND" in eval_source
