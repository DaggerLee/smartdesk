from unittest.mock import patch

from agent.groundedness import check
from llm.client import LLMResponse


EVIDENCE = [{"text": "The sky is blue.", "source": "facts.txt"}]


def _response(text: str) -> LLMResponse:
    return LLMResponse(text=text, tool_calls=[], raw={})


def test_no_evidence_is_not_applicable_without_judge_call():
    with patch("agent.groundedness.complete") as judge:
        result = check("Common knowledge.", [])

    judge.assert_not_called()
    assert result == {
        "supported": True,
        "unsupported_sentences": [],
        "verification_status": "not_applicable",
    }


def test_valid_supported_result_is_verified():
    with patch(
        "agent.groundedness.complete",
        return_value=_response('{"supported": true, "unsupported_sentences": []}'),
    ):
        result = check("The sky is blue.", EVIDENCE)

    assert result["supported"] is True
    assert result["verification_status"] == "verified"


def test_valid_unsupported_result_is_rejected():
    with patch(
        "agent.groundedness.complete",
        return_value=_response(
            '{"supported": false, "unsupported_sentences": ["The sky is green."]}'
        ),
    ):
        result = check("The sky is green.", EVIDENCE)

    assert result["supported"] is False
    assert result["unsupported_sentences"] == ["The sky is green."]
    assert result["verification_status"] == "rejected"


def test_parse_failure_is_check_error_not_verified():
    with patch("agent.groundedness.complete", return_value=_response("not json")):
        result = check("Answer.", EVIDENCE)

    assert result["verification_status"] == "check_error"


def test_judge_exception_is_check_error_not_request_failure():
    with patch("agent.groundedness.complete", side_effect=RuntimeError("judge unavailable")):
        result = check("Answer.", EVIDENCE)

    assert result["verification_status"] == "check_error"
