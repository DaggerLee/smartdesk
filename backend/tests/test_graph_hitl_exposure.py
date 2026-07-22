from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from agent.graph import stream_graph
from llm.client import LLMResponse


@pytest.mark.parametrize(
    ("query", "enabled"),
    [
        ("Save this as a Markdown note", False),
        ("What is retrieval augmented generation?", True),
    ],
)
def test_write_note_is_not_exposed_unless_flag_and_explicit_intent_both_match(
    query: str, enabled: bool
) -> None:
    declarations: list[list[dict]] = []

    def answer(messages, tools=None, system=None):
        declarations.append(tools or [])
        return LLMResponse(text="Answer", tool_calls=[], raw={})

    with (
        patch("agent.graph.route", return_value="agent"),
        patch("agent.graph.complete", side_effect=answer),
        patch("agent.graph.is_hitl_write_note_enabled", return_value=enabled),
        patch(
            "agent.graph._check_groundedness",
            return_value={"supported": True, "unsupported_sentences": []},
        ),
    ):
        list(
            stream_graph(
                query,
                kb_id=1,
                user_id=7,
                thread_id=f"exposure-{uuid.uuid4().hex}",
            )
        )

    assert all(item.get("name") != "write_note" for item in declarations[0])
