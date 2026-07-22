from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agent.delivery import NON_CONTEXT_ANSWERS
from agent.write_note_policy import LEGACY_WRITE_UNAVAILABLE_NOTICE
from database import Base
from models import Conversation
from routers import chat


class _Query:
    def filter(self, *args):
        return self

    def order_by(self, *args):
        return self

    def limit(self, value):
        return self

    def all(self):
        return []


class _DB:
    def __init__(self):
        self.conversations: list[Conversation] = []
        self.commits = 0

    def query(self, model):
        return _Query()

    def add(self, conversation):
        self.conversations.append(conversation)

    def commit(self):
        self.commits += 1


def _decode_text_frames(frames: list[str]) -> list[str]:
    return [
        json.loads(frame.removeprefix("data: ").strip())
        for frame in frames
        if frame != "data: [DONE]\n\n"
    ]


@pytest.mark.parametrize(
    ("backend", "enabled", "expected_owner"),
    [
        ("legacy", "false", "legacy"),
        ("legacy", "true", "fallback"),
        ("langgraph", "false", "graph"),
        ("langgraph", "true", "graph"),
    ],
)
def test_backend_and_hitl_flag_matrix_preserves_only_emergency_fallback(
    backend: str,
    enabled: str,
    expected_owner: str,
) -> None:
    db = _DB()
    route_mock = MagicMock(return_value="direct")
    graph_mock = MagicMock(
        return_value=iter([
            chat.GraphEvent(type="chunk", data={"text": "graph answer"}),
            chat.GraphEvent(
                type="final",
                data={"route": "direct", "answer": "graph answer"},
            )
        ])
    )
    with (
        patch.dict(
            "os.environ",
            {
                "SMARTDESK_AGENT_BACKEND": backend,
                "SMARTDESK_HITL_WRITE_NOTE": enabled,
            },
            clear=False,
        ),
        patch("routers.chat._owned_kb"),
        patch("routers.chat.route", route_mock),
        patch("routers.chat._stream_graph_with_proposal_failure", graph_mock),
        patch("routers.chat.llm_stream", return_value=iter(["legacy answer"])),
        patch(
            "routers.chat.StreamingResponse",
            side_effect=lambda content, **kwargs: list(content),
        ),
    ):
        frames = chat.chat_stream(
            chat.ChatRequest(kb_id=1, message="Save this summary as a note file."),
            db=db,
            current_user=SimpleNamespace(id=7),
        )

    assert frames[-1] == "data: [DONE]\n\n"
    if expected_owner == "fallback":
        assert _decode_text_frames(frames) == [LEGACY_WRITE_UNAVAILABLE_NOTICE]
        assert route_mock.call_count == 0
        assert graph_mock.call_count == 0
    elif expected_owner == "legacy":
        assert _decode_text_frames(frames) == ["legacy answer"]
        assert route_mock.call_count == 1
        assert graph_mock.call_count == 0
    else:
        assert _decode_text_frames(frames) == ["graph answer"]
        assert route_mock.call_count == 0
        assert graph_mock.call_count == 1


def test_emergency_fallback_has_no_model_graph_or_filesystem_side_effect(tmp_path) -> None:
    db = _DB()
    with (
        patch.dict(
            "os.environ",
            {
                "SMARTDESK_AGENT_BACKEND": "legacy",
                "SMARTDESK_HITL_WRITE_NOTE": "true",
                "SMARTDESK_DATA_DIR": str(tmp_path),
            },
            clear=False,
        ),
        patch("routers.chat._owned_kb"),
        patch("routers.chat.route") as route_mock,
        patch("routers.chat.run_agent") as agent_mock,
        patch("routers.chat.stream_graph") as graph_mock,
        patch("routers.chat.llm_stream") as llm_mock,
        patch(
            "routers.chat.StreamingResponse",
            side_effect=lambda content, **kwargs: list(content),
        ),
    ):
        frames = chat.chat_stream(
            chat.ChatRequest(kb_id=1, message="Write this to a Markdown note."),
            db=db,
            current_user=SimpleNamespace(id=7),
        )

    assert _decode_text_frames(frames) == [LEGACY_WRITE_UNAVAILABLE_NOTICE]
    assert db.commits == 1
    assert db.conversations[0].answer == LEGACY_WRITE_UNAVAILABLE_NOTICE
    assert route_mock.call_count == 0
    assert agent_mock.call_count == 0
    assert graph_mock.call_count == 0
    assert llm_mock.call_count == 0
    assert list(tmp_path.iterdir()) == []


def test_legacy_notice_is_visible_in_history_but_excluded_before_context_limit() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    started = datetime(2026, 7, 22, 12, 0, 0)
    try:
        for index in range(6):
            session.add(Conversation(
                kb_id=1,
                question=f"q{index}",
                answer=f"usable-{index}",
                created_at=started + timedelta(minutes=index),
            ))
        session.add(Conversation(
            kb_id=1,
            question="save this",
            answer=LEGACY_WRITE_UNAVAILABLE_NOTICE,
            created_at=started + timedelta(minutes=6),
        ))
        session.commit()

        with patch("routers.chat._owned_kb"):
            visible = chat.get_history(1, session, SimpleNamespace(id=7))
        context = chat._recent_usable_history(session, kb_id=1)

        assert visible[-1].answer == LEGACY_WRITE_UNAVAILABLE_NOTICE
        assert [row.answer for row in context] == [
            "usable-5",
            "usable-4",
            "usable-3",
            "usable-2",
            "usable-1",
        ]
        assert LEGACY_WRITE_UNAVAILABLE_NOTICE in NON_CONTEXT_ANSWERS
    finally:
        session.close()
