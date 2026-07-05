import os

# ── Retrieval ─────────────────────────────────────────────────────────────────
TOP_K: int = 5
CHUNK_SIZE: int = 800
CHUNK_OVERLAP: int = 100
RELEVANCE_THRESHOLD: float = 0.8  # cosine distance; below this = relevant

# ── Agent loop ────────────────────────────────────────────────────────────────
MAX_AGENT_TURNS: int = 5

# ── LLM / Gemini ─────────────────────────────────────────────────────────────
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
GEMINI_BASE_URL: str = "https://generativelanguage.googleapis.com/v1beta"

# ── Tracing ───────────────────────────────────────────────────────────────────
TRACE_LOG_PATH: str = os.getenv("TRACE_LOG_PATH", "logs/traces/traces.jsonl")
