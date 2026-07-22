import json
from unittest.mock import patch

import pytest

from agent.delivery import UNSUPPORTED_ANSWER_NOTICE
from eval import run_eval


def _item(item_id, status):
    return run_eval.ItemResult(
        id=item_id,
        query="q",
        category="comparison",
        difficulty="medium",
        expected_route="agent",
        actual_route="agent",
        route_correct=True,
        is_boundary=False,
        verification_status=status,
    )


def test_langgraph_runner_returns_verification_status():
    final_state = {
        "route": "agent",
        "answer": "answer",
        "grounded": True,
        "verification_status": "verified",
        "evidence": [{"text": "chunk"}],
    }
    with patch("eval.run_eval._run_graph", return_value=final_state):
        answer, chunks, grounded, status = run_eval._run_agent_path_graph("q", kb_id=1)

    assert answer == "answer"
    assert chunks == ["chunk"]
    assert grounded is True
    assert status == "verified"


def test_aggregate_records_verification_status_distribution():
    aggregate = run_eval.aggregate([
        _item("a1", "verified"),
        _item("a2", "verified"),
        _item("a3", "rejected"),
        _item("r1", None),
    ])

    assert aggregate["verification_status_distribution"] == {
        "rejected": 1,
        "verified": 2,
    }


def test_history_archive_preserves_status_distribution(tmp_path):
    aggregate = run_eval.aggregate([
        _item("a1", "not_applicable"),
        _item("a2", "check_error"),
    ])
    history_path = tmp_path / "history.jsonl"

    with patch.object(run_eval, "HISTORY_PATH", history_path), \
         patch("eval.run_eval._git_commit", return_value="abc1234"), \
         patch.object(run_eval, "_AGENT_BACKEND", "langgraph"):
        run_eval.append_history(
            aggregate,
            n_items=2,
            limit=None,
            git_dirty=False,
        )

    record = json.loads(history_path.read_text().strip())
    assert record["verification_status_distribution"] == {
        "check_error": 1,
        "not_applicable": 1,
    }


def test_enabled_langgraph_eval_scores_the_payload_users_receive(monkeypatch):
    item = {
        "id": "a1",
        "query": "q",
        "category": "comparison",
        "difficulty": "medium",
        "expected_route": "agent",
        "kb_id": 1,
        "expected_answer_contains": [],
        "min_hits": 0,
        "grounding_required": False,
    }
    monkeypatch.setenv("SMARTDESK_VERIFIED_AGENT_DELIVERY", "1")

    with patch.object(run_eval, "_AGENT_BACKEND", "langgraph"), \
         patch("eval.run_eval._router_route", return_value="agent"), \
         patch(
             "eval.run_eval._run_agent_path",
             return_value=("raw rejected answer", [], False, "rejected"),
         ), \
         patch("eval.run_eval._faithfulness", return_value=1.0), \
         patch("eval.run_eval._answer_relevancy", return_value=1.0):
        result = run_eval.eval_item(item)

    assert result.answer == UNSUPPORTED_ANSWER_NOTICE
    assert result.answer_scope == "production_delivered"
    assert result.delivery_kind == "unsupported_notice"


def test_disabled_langgraph_eval_labels_internal_answer(monkeypatch):
    item = {
        "id": "a1",
        "query": "q",
        "category": "comparison",
        "difficulty": "medium",
        "expected_route": "agent",
        "kb_id": 1,
        "expected_answer_contains": [],
        "min_hits": 0,
        "grounding_required": False,
    }
    monkeypatch.setenv("SMARTDESK_VERIFIED_AGENT_DELIVERY", "0")

    with patch.object(run_eval, "_AGENT_BACKEND", "langgraph"), \
         patch("eval.run_eval._router_route", return_value="agent"), \
         patch(
             "eval.run_eval._run_agent_path",
             return_value=("internal answer", [], True, "verified"),
         ), \
         patch("eval.run_eval._faithfulness", return_value=1.0), \
         patch("eval.run_eval._answer_relevancy", return_value=1.0):
        result = run_eval.eval_item(item)

    assert result.answer == "internal answer"
    assert result.answer_scope == "agent_internal"
    assert result.delivery_kind is None


@pytest.mark.parametrize("route", ["direct", "rag"])
def test_simplified_non_agent_eval_is_labeled(route):
    item = {
        "id": "q1",
        "query": "q",
        "category": "unanswerable" if route == "rag" else "comparison",
        "difficulty": "medium",
        "expected_route": route,
        "kb_id": 1,
        "expected_answer_contains": [],
        "min_hits": 0,
        "grounding_required": False,
    }

    with patch("eval.run_eval._router_route", return_value=route), \
         patch("eval.run_eval._run_direct", return_value="answer"), \
         patch("eval.run_eval._run_rag", return_value="answer"):
        result = run_eval.eval_item(item)

    assert result.answer_scope == "eval_simplified"


def test_aggregate_records_answer_scope_distribution():
    delivered = _item("a1", "verified")
    delivered.answer_scope = "production_delivered"
    delivered.delivery_kind = "graph_answer"
    internal = _item("a2", "verified")
    internal.answer_scope = "agent_internal"

    aggregate = run_eval.aggregate([delivered, internal])

    assert aggregate["answer_scope_distribution"] == {
        "agent_internal": 1,
        "production_delivered": 1,
    }
    assert aggregate["delivery_kind_distribution"] == {"graph_answer": 1}
