from agent.delivery import (
    NON_CONTEXT_ANSWERS,
    RETRYABLE_VERIFICATION_NOTICE,
    UNSUPPORTED_ANSWER_NOTICE,
    is_verified_delivery_enabled,
    select_delivery,
)
from agent.write_note_policy import LEGACY_WRITE_UNAVAILABLE_NOTICE


def test_allowed_statuses_deliver_graph_answer():
    for status in ("verified", "not_applicable"):
        decision = select_delivery("graph answer", status)
        assert decision.payload == "graph answer"
        assert decision.kind == "graph_answer"


def test_retryable_statuses_share_one_notice():
    for status in ("check_error", "unchecked_max_turns"):
        decision = select_delivery("blocked raw answer", status)
        assert decision.payload == RETRYABLE_VERIFICATION_NOTICE
        assert decision.kind == "retryable_notice"


def test_rejected_missing_and_unknown_status_fail_closed():
    for status in ("rejected", None, "future_status"):
        decision = select_delivery("blocked raw answer", status)
        assert decision.payload == UNSUPPORTED_ANSWER_NOTICE
        assert decision.kind == "unsupported_notice"


def test_notice_literals_are_stable_and_excluded_from_context():
    assert RETRYABLE_VERIFICATION_NOTICE == (
        "I couldn't complete a verifiable answer this time. Please try again."
    )
    assert UNSUPPORTED_ANSWER_NOTICE == (
        "I couldn't provide an answer that was sufficiently supported by the available evidence."
    )
    assert NON_CONTEXT_ANSWERS == frozenset({
        LEGACY_WRITE_UNAVAILABLE_NOTICE,
        RETRYABLE_VERIFICATION_NOTICE,
        UNSUPPORTED_ANSWER_NOTICE,
    })


def test_feature_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("SMARTDESK_VERIFIED_AGENT_DELIVERY", raising=False)
    assert is_verified_delivery_enabled() is False


def test_feature_flag_is_read_at_call_time(monkeypatch):
    monkeypatch.setenv("SMARTDESK_VERIFIED_AGENT_DELIVERY", "true")
    assert is_verified_delivery_enabled() is True

    monkeypatch.setenv("SMARTDESK_VERIFIED_AGENT_DELIVERY", "0")
    assert is_verified_delivery_enabled() is False
