from __future__ import annotations

import os
import re
from typing import Literal

from config import HITL_WRITE_NOTE_DEFAULT, HITL_WRITE_NOTE_ENV_VAR


WriteIntent = Literal["none", "draft", "persist"]

LEGACY_WRITE_UNAVAILABLE_NOTICE = (
    "Saving notes is unavailable while SmartDesk is running in legacy mode. "
    "No file was created."
)

WRITE_NOTE_POLICY = """\
## Writing notes
- Use write_note only when the request has explicit persist intent.
- Call write_note in a tool-call round by itself, with only title and content.
- Never invent a file path or claim success without a returned action receipt.
"""

TITLE_MAX_CHARS = 120
CONTENT_MAX_CHARS = 50_000
REJECT_REASON_MAX_CHARS = 500

_EN_DRAFT = re.compile(
    r"(?:\bdraft\b.*\b(?:only|do\s+not\s+save|don't\s+save)\b|"
    r"\bdo\s+not\s+save\b.*\b(?:note|draft)\b)",
    re.IGNORECASE,
)
_EN_PERSIST = re.compile(
    r"(?:\bsave\b.{0,80}\b(?:note|markdown|file)\b|"
    r"\bwrite\b.{0,80}\b(?:to|into|as)\b.{0,40}\b(?:note|markdown\s+note|note\s+file)\b)",
    re.IGNORECASE,
)

# Chinese rules intentionally avoid regex word boundaries: Python's `\b`
# semantics do not describe Chinese token boundaries.
_ZH_DRAFT_MARKERS = ("不要保存", "不保存", "只生成", "仅生成", "不写入文件", "草稿")
_ZH_NOTE_MARKERS = ("笔记", "Markdown", "markdown")
_ZH_PERSIST_PATTERNS = (
    re.compile(r"(?:保存成|保存为).{0,30}(?:笔记|Markdown|markdown)(?:文件)?"),
    re.compile(r"写入.{0,30}(?:笔记|Markdown|markdown)(?:文件)"),
)


def classify_write_intent(text: str) -> WriteIntent:
    if any(marker in text for marker in _ZH_DRAFT_MARKERS) and any(
        marker in text for marker in _ZH_NOTE_MARKERS
    ):
        return "draft"
    if _EN_DRAFT.search(text):
        return "draft"
    if any(pattern.search(text) for pattern in _ZH_PERSIST_PATTERNS):
        return "persist"
    if _EN_PERSIST.search(text):
        return "persist"
    return "none"


def is_hitl_write_note_enabled() -> bool:
    raw = os.getenv(HITL_WRITE_NOTE_ENV_VAR, str(HITL_WRITE_NOTE_DEFAULT))
    return raw.strip().lower() in {"1", "true", "yes", "on"}
