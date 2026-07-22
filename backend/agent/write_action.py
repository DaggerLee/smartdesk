from __future__ import annotations

import unicodedata
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent.write_note_policy import (
    CONTENT_MAX_CHARS,
    REJECT_REASON_MAX_CHARS,
    TITLE_MAX_CHARS,
)


ActionStatus = Literal[
    "proposed",
    "approved",
    "rejected",
    "succeeded",
    "replayed",
    "conflict",
    "failed",
]
ACTION_STATUSES: tuple[ActionStatus, ...] = (
    "proposed",
    "approved",
    "rejected",
    "succeeded",
    "replayed",
    "conflict",
    "failed",
)


class _StrictImmutableModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _validate_action_id(value: str) -> str:
    if not value or value != value.strip() or "\x00" in value:
        raise ValueError("action_id must be non-blank, trimmed, and contain no NUL")
    return value


def _validate_title(value: str) -> str:
    if not value or value != value.strip() or value in {".", ".."}:
        raise ValueError("title must be non-blank and trimmed")
    if "/" in value or "\\" in value or any(
        unicodedata.category(character).startswith("C") for character in value
    ):
        raise ValueError("title contains unsafe characters")
    return value


def _validate_content(value: str) -> str:
    if not value.strip() or "\x00" in value:
        raise ValueError("content must be non-blank and contain no NUL")
    return value


class WriteNotePayload(_StrictImmutableModel):
    title: str = Field(max_length=TITLE_MAX_CHARS)
    content: str = Field(max_length=CONTENT_MAX_CHARS)

    _safe_title = field_validator("title")(_validate_title)
    _safe_content = field_validator("content")(_validate_content)


def validate_write_note_payload(title: str, content: str) -> WriteNotePayload:
    return WriteNotePayload(title=title, content=content)


class ApproveResolution(_StrictImmutableModel):
    action_id: str
    decision: Literal["approve"]

    _valid_action_id = field_validator("action_id")(_validate_action_id)


class EditResolution(_StrictImmutableModel):
    action_id: str
    decision: Literal["edit"]
    title: str = Field(max_length=TITLE_MAX_CHARS)
    content: str = Field(max_length=CONTENT_MAX_CHARS)

    _valid_action_id = field_validator("action_id")(_validate_action_id)
    _safe_title = field_validator("title")(_validate_title)
    _safe_content = field_validator("content")(_validate_content)


class RejectResolution(_StrictImmutableModel):
    action_id: str
    decision: Literal["reject"]
    reason: str | None = Field(default=None, max_length=REJECT_REASON_MAX_CHARS)

    _valid_action_id = field_validator("action_id")(_validate_action_id)

    @field_validator("reason")
    @classmethod
    def _valid_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value or value != value.strip() or "\x00" in value:
            raise ValueError("reason must be non-blank, trimmed, and contain no NUL")
        return value


ActionResolution = Annotated[
    ApproveResolution | EditResolution | RejectResolution,
    Field(discriminator="decision"),
]


class ActionReceipt(_StrictImmutableModel):
    action_id: str
    tool: Literal["write_note"] = "write_note"
    result: Literal["succeeded", "replayed", "rejected", "conflict", "failed"]
    relative_path: str | None = None
    content_hash: str | None = None
    byte_count: int | None = Field(default=None, ge=0)
    read_back_verified: bool = False
    error_code: str | None = None

    _valid_action_id = field_validator("action_id")(_validate_action_id)


class PendingAction(_StrictImmutableModel):
    action_id: str
    user_id: int = Field(gt=0)
    tool: Literal["write_note"] = "write_note"
    original_payload: WriteNotePayload
    approved_payload: WriteNotePayload | None = None
    decision: Literal["approve", "edit", "reject"] | None = None
    reject_reason: str | None = Field(default=None, max_length=REJECT_REASON_MAX_CHARS)
    status: ActionStatus = "proposed"
    receipt: ActionReceipt | None = None
    error: str | None = None

    _valid_action_id = field_validator("action_id")(_validate_action_id)

    @field_validator("reject_reason")
    @classmethod
    def _valid_reject_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value or value != value.strip() or "\x00" in value:
            raise ValueError("reject_reason must be non-blank, trimmed, and contain no NUL")
        return value


_ACTION_ANSWER_TEMPLATES = {
    "en": {
        "succeeded": "The note was saved to {relative_path}.",
        "replayed": "The note was already saved to {relative_path}; no duplicate was created.",
        "rejected": "The note was not saved because the action was rejected.",
        "conflict": "The note could not be saved because the existing file conflicts with this action.",
        "failed": "The note could not be saved.",
    },
    "zh": {
        "succeeded": "笔记已保存到 {relative_path}。",
        "replayed": "笔记已存在于 {relative_path}，未创建重复文件。",
        "rejected": "保存操作已拒绝，未创建文件。",
        "conflict": "保存发生冲突，未覆盖现有文件。",
        "failed": "笔记保存失败。",
    },
}


def render_action_answer(
    receipt: ActionReceipt, language: Literal["en", "zh"] = "en"
) -> str:
    return _ACTION_ANSWER_TEMPLATES[language][receipt.result].format(
        relative_path=receipt.relative_path or "the assigned note path"
    )


def to_action_evidence(receipt: ActionReceipt) -> dict[str, object]:
    return {
        "type": "action_receipt",
        "action_id": receipt.action_id,
        "tool": receipt.tool,
        "result": receipt.result,
        "relative_path": receipt.relative_path,
        "content_hash": receipt.content_hash,
        "byte_count": receipt.byte_count,
        "read_back_verified": receipt.read_back_verified,
        "error_code": receipt.error_code,
    }
