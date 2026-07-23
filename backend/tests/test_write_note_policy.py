from __future__ import annotations

import pytest

from agent.write_note_policy import classify_write_intent, is_hitl_write_note_enabled


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Save this summary as a note file.", "persist"),
        ("Write this to a Markdown note.", "persist"),
        ("把这份总结保存成 Markdown 笔记文件。", "persist"),
        ("将上面的内容写入笔记文件。", "persist"),
        ("Draft a note, but do not save it.", "draft"),
        ("Write a note draft only.", "draft"),
        ("帮我写一份笔记草稿，不要保存。", "draft"),
        ("只生成笔记内容，不写入文件。", "draft"),
        ("record this", "none"),
        ("remember this", "none"),
        ("note that the deadline is Friday", "none"),
        ("记录一下", "none"),
        ("记住这个", "none"),
        ("请注意截止日期是周五", "none"),
        ("What is MCP?", "none"),
    ],
)
def test_classify_write_intent_is_high_precision(text: str, expected: str):
    assert classify_write_intent(text) == expected


def test_hitl_write_note_defaults_on(monkeypatch):
    monkeypatch.delenv("SMARTDESK_HITL_WRITE_NOTE", raising=False)

    assert is_hitl_write_note_enabled() is True


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_hitl_write_note_accepts_explicit_true_values(monkeypatch, value: str):
    monkeypatch.setenv("SMARTDESK_HITL_WRITE_NOTE", value)

    assert is_hitl_write_note_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "off", "", "unexpected"])
def test_hitl_write_note_fails_closed_for_other_values(monkeypatch, value: str):
    monkeypatch.setenv("SMARTDESK_HITL_WRITE_NOTE", value)

    assert is_hitl_write_note_enabled() is False
