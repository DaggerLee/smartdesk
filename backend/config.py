import os
from pathlib import Path

# ── Retrieval ─────────────────────────────────────────────────────────────────
TOP_K: int = 5
CHUNK_SIZE: int = 800
CHUNK_OVERLAP: int = 100
# cosine distance; below this = relevant. Recalibrated 2026-07-11 after the
# embedding model swap to paraphrase-multilingual-MiniLM-L12-v2 (old 0.8 was
# tuned for the English-only DefaultEmbeddingFunction, under which Chinese
# queries always scored ~0.85-1.9 — relevance_ok was permanently ~0%).
RELEVANCE_THRESHOLD: float = 0.62

# ── Agent backend ─────────────────────────────────────────────────────────────
AGENT_BACKEND_ENV_VAR: str = "SMARTDESK_AGENT_BACKEND"
AGENT_BACKEND_DEFAULT: str = "legacy"
VALID_AGENT_BACKENDS: frozenset[str] = frozenset({"legacy", "langgraph"})


def get_agent_backend() -> str:
    value = os.getenv(AGENT_BACKEND_ENV_VAR, AGENT_BACKEND_DEFAULT)
    if value not in VALID_AGENT_BACKENDS:
        raise ValueError(
            "SMARTDESK_AGENT_BACKEND must be exactly 'legacy' or 'langgraph'"
        )
    return value


# Validate at import/startup; request-time consumers call the same owner so a
# later process-environment mutation cannot silently bypass validation.
AGENT_BACKEND: str = get_agent_backend()


# ── Agent loop ────────────────────────────────────────────────────────────────
MAX_AGENT_TURNS: int = 5

# Phase A keeps write-note HITL disabled unless explicitly enabled.
HITL_WRITE_NOTE_ENV_VAR: str = "SMARTDESK_HITL_WRITE_NOTE"
HITL_WRITE_NOTE_DEFAULT: bool = False

# The Docker deployment mounts its persistent volume at /app/data. With /app
# as the working directory, this relative default stays inside that volume.
WRITE_NOTE_ROOT_ENV_VAR: str = "SMARTDESK_DATA_DIR"
WRITE_NOTE_ROOT_DEFAULT: Path = Path("data")
WRITE_NOTE_ROOT: Path = Path(os.getenv(WRITE_NOTE_ROOT_ENV_VAR, WRITE_NOTE_ROOT_DEFAULT))

# ── LLM / Gemini ─────────────────────────────────────────────────────────────
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
GEMINI_BASE_URL: str = "https://generativelanguage.googleapis.com/v1beta"
# Single source of truth for the model. Exact name (no -latest alias); clients
# validate availability at first call but never auto-pick a different model.
GEMINI_MODEL: str = "models/gemini-3.5-flash"

# ── Tracing ───────────────────────────────────────────────────────────────────
TRACE_LOG_PATH: str = os.getenv("TRACE_LOG_PATH", "logs/traces/traces.jsonl")

# ── Checkpointing (W5 T4) ───────────────────────────────────────────────────────
# LangGraph checkpoint store — a separate sqlite file from the business DB
# (database.py's smartdesk.db), so per-turn agent execution state never shares
# a schema or a lock with KB/conversation/user records. Env override lets
# tests point this at an isolated file instead of data/ (see tests/conftest.py).
CHECKPOINT_DB_PATH: str = os.getenv("CHECKPOINT_DB_PATH", "data/checkpoints.sqlite")
