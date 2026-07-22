import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.delivery import (
    RETRYABLE_VERIFICATION_NOTICE,
    UNSUPPORTED_ANSWER_NOTICE,
)
from agent.graph import GraphEvent
from llm.client import LLMResponse
from routers import chat


class _EmptyQuery:
    def filter(self, *args):
        return self

    def order_by(self, *args):
        return self

    def limit(self, value):
        return self

    def all(self):
        return []


class _RequestDB:
    def query(self, model):
        return _EmptyQuery()


class _DeliveryDB:
    def __init__(self, events, commit_error=None):
        self.events = events
        self.commit_error = commit_error
        self.conversation = None

    def add(self, conversation):
        self.events.append("add")
        self.conversation = conversation

    def commit(self):
        self.events.append("commit")
        if self.commit_error:
            raise self.commit_error

    def close(self):
        self.events.append("close")


def _stream_for(final_state, delivery_db, trace_events, llm_stream=None, verified_flag="1"):
    body = chat.ChatRequest(kb_id=1, message="question")
    graph_events = [GraphEvent(type="final", data=final_state)]
    llm_stream = llm_stream or MagicMock(side_effect=AssertionError("unexpected llm_stream"))

    with patch.dict(
        "os.environ",
        {
            "SMARTDESK_AGENT_BACKEND": "langgraph",
            "SMARTDESK_VERIFIED_AGENT_DELIVERY": verified_flag,
        },
        clear=False,
    ), patch("routers.chat._owned_kb"), \
         patch("routers.chat.stream_graph", return_value=iter(graph_events)), \
         patch("routers.chat.SessionLocal", return_value=delivery_db), \
         patch("routers.chat.llm_stream", llm_stream), \
         patch("routers.chat._trace_write", side_effect=lambda entry: trace_events.append(entry), create=True), \
         patch("routers.chat.StreamingResponse", side_effect=lambda content, **kwargs: content):
        yield from chat.chat_stream(
            body,
            db=_RequestDB(),
            current_user=SimpleNamespace(id=7),
        )


def _agent_state(status, answer="graph answer"):
    return {
        "route": "agent",
        "answer": answer,
        "messages": [{"role": "user", "parts": [{"text": "question"}]}],
        "verification_status": status,
    }


def _decode_answer(frame):
    assert frame.startswith("data: ")
    return json.loads(frame.removeprefix("data: ").strip())


def _zero_tool_response():
    return LLMResponse(
        text="Direct agent answer.",
        tool_calls=[],
        raw={
            "candidates": [{
                "content": {
                    "role": "model",
                    "parts": [{"text": "Direct agent answer."}],
                }
            }]
        },
    )


def _stream_real_zero_tool_graph(delivery_db, trace_events, llm_stream, verified_flag):
    body = chat.ChatRequest(kb_id=1, message="hello")
    with patch.dict(
        "os.environ",
        {
            "SMARTDESK_AGENT_BACKEND": "langgraph",
            "SMARTDESK_VERIFIED_AGENT_DELIVERY": verified_flag,
        },
        clear=False,
    ), patch("routers.chat._owned_kb"), \
         patch("agent.graph.route", return_value="agent"), \
         patch("agent.graph.complete", return_value=_zero_tool_response()), \
         patch("routers.chat.SessionLocal", return_value=delivery_db), \
         patch("routers.chat.llm_stream", llm_stream), \
         patch("routers.chat._trace_write", side_effect=trace_events.append), \
         patch("routers.chat.StreamingResponse", side_effect=lambda content, **kwargs: content):
        return list(chat.chat_stream(
            body,
            db=_RequestDB(),
            current_user=SimpleNamespace(id=7),
        ))


@pytest.mark.parametrize("status", ["verified", "not_applicable"])
def test_enabled_allowed_answer_is_committed_before_identical_frame(status):
    events = []
    traces = []
    delivery_db = _DeliveryDB(events)
    generator = _stream_for(_agent_state(status), delivery_db, traces)

    answer_frame = next(generator)

    assert events == ["add", "commit", "close"]
    assert _decode_answer(answer_frame) == "graph answer"
    assert delivery_db.conversation.answer == "graph answer"
    assert next(generator) == "data: [DONE]\n\n"
    with pytest.raises(StopIteration):
        next(generator)
    assert traces[0]["post_graph_generation_calls"] == 0
    assert traces[0]["graph_answer_matches_persisted"] is True


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("check_error", RETRYABLE_VERIFICATION_NOTICE),
        ("unchecked_max_turns", RETRYABLE_VERIFICATION_NOTICE),
        ("rejected", UNSUPPORTED_ANSWER_NOTICE),
        (None, UNSUPPORTED_ANSWER_NOTICE),
        ("unknown", UNSUPPORTED_ANSWER_NOTICE),
    ],
)
def test_enabled_blocked_status_persists_and_emits_only_notice(status, expected):
    events = []
    delivery_db = _DeliveryDB(events)
    generator = _stream_for(_agent_state(status, answer="raw blocked answer"), delivery_db, [])

    assert _decode_answer(next(generator)) == expected
    assert delivery_db.conversation.answer == expected
    assert "raw blocked answer" not in delivery_db.conversation.answer


def test_commit_failure_emits_no_answer_and_writes_no_success_trace():
    events = []
    traces = []
    delivery_db = _DeliveryDB(events, commit_error=RuntimeError("db unavailable"))
    generator = _stream_for(_agent_state("verified"), delivery_db, traces)

    with pytest.raises(RuntimeError, match="db unavailable"):
        next(generator)

    assert events == ["add", "commit", "close"]
    assert traces == []


def test_flag_off_keeps_one_post_graph_generation_and_traces_committed_payload():
    events = []
    traces = []
    delivery_db = _DeliveryDB(events)
    stream_mock = MagicMock(return_value=iter(["regen", " answer"]))

    generator = _stream_for(_agent_state("verified"), delivery_db, traces, llm_stream=stream_mock, verified_flag="0")
    frames = list(generator)

    assert [_decode_answer(frame) for frame in frames[:-1]] == ["regen", " answer"]
    assert delivery_db.conversation.answer == "regen answer"
    assert stream_mock.call_count == 1
    assert traces[0]["post_graph_generation_calls"] == 1
    assert traces[0]["delivery_kind"] == "regenerated_answer"


def test_real_zero_tool_graph_flag_off_regenerates_non_empty_answer():
    events = []
    traces = []
    delivery_db = _DeliveryDB(events)
    stream_mock = MagicMock(return_value=iter(["Regenerated answer."]))

    frames = _stream_real_zero_tool_graph(
        delivery_db,
        traces,
        llm_stream=stream_mock,
        verified_flag="0",
    )

    assert [_decode_answer(frame) for frame in frames[:-1]] == ["Regenerated answer."]
    assert delivery_db.conversation.answer == "Regenerated answer."
    assert stream_mock.call_count == 1


def test_real_zero_tool_graph_flag_on_delivers_checked_answer_without_regeneration():
    events = []
    traces = []
    delivery_db = _DeliveryDB(events)
    stream_mock = MagicMock(side_effect=AssertionError("unexpected llm_stream"))

    frames = _stream_real_zero_tool_graph(
        delivery_db,
        traces,
        llm_stream=stream_mock,
        verified_flag="1",
    )

    assert [_decode_answer(frame) for frame in frames[:-1]] == ["Direct agent answer."]
    assert delivery_db.conversation.answer == "Direct agent answer."
    assert traces[0]["verification_status"] == "not_applicable"
    assert traces[0]["post_graph_generation_calls"] == 0


def test_real_streaming_response_finishes_without_cross_context_trace_error(tmp_path):
    events = []
    delivery_db = _DeliveryDB(events)
    body = chat.ChatRequest(kb_id=1, message="question")
    graph_events = [
        GraphEvent(type="tool_call", data={"name": "retrieve", "args": {}}),
        GraphEvent(type="final", data=_agent_state("verified")),
    ]
    trace_path = tmp_path / "traces.jsonl"
    app = FastAPI()

    @app.get("/stream")
    def stream():
        return chat.chat_stream(
            body,
            db=_RequestDB(),
            current_user=SimpleNamespace(id=7),
        )

    with patch.dict(
        "os.environ",
        {
            "SMARTDESK_AGENT_BACKEND": "langgraph",
            "SMARTDESK_VERIFIED_AGENT_DELIVERY": "1",
        },
        clear=False,
    ), patch("routers.chat._owned_kb"), \
         patch("routers.chat.stream_graph", return_value=iter(graph_events)), \
         patch("routers.chat.SessionLocal", return_value=delivery_db), \
         patch("llm.trace._log_path", trace_path):
        response = TestClient(app).get("/stream")

    assert response.status_code == 200
    assert response.text.endswith("data: [DONE]\n\n")
    delivery_trace = next(
        json.loads(line)
        for line in trace_path.read_text().splitlines()
        if '"agent_delivery"' in line
    )
    assert delivery_trace["request_id"]
