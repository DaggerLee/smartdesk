from __future__ import annotations

import uuid
from unittest.mock import patch

from agent.graph import run_graph
from llm.client import LLMResponse


def test_ordinary_grounded_answer_records_llm_groundedness_source() -> None:
    with (
        patch("agent.graph.route", return_value="agent"),
        patch(
            "agent.graph.complete",
            return_value=LLMResponse(text="Grounded answer", tool_calls=[], raw={}),
        ),
        patch(
            "agent.graph._check_groundedness",
            return_value={"supported": True, "unsupported_sentences": []},
        ),
    ):
        state = run_graph(
            "What is retrieval?",
            kb_id=1,
            thread_id=f"verification-source-{uuid.uuid4().hex}",
        )

    assert state["verification_status"] == "verified"
    assert state["verification_source"] == "llm_groundedness"
