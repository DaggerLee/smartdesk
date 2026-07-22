from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agent.delivery import (
    RETRYABLE_VERIFICATION_NOTICE,
    UNSUPPORTED_ANSWER_NOTICE,
)
from database import Base
from models import Conversation
from routers.chat import _recent_usable_history


def test_notices_are_excluded_before_five_turn_limit():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    started = datetime(2026, 7, 21, 12, 0, 0)

    try:
        for index in range(7):
            session.add(Conversation(
                kb_id=1,
                question=f"q{index}",
                answer=f"usable-{index}",
                created_at=started + timedelta(minutes=index),
            ))
        session.add(Conversation(
            kb_id=1,
            question="retry",
            answer=RETRYABLE_VERIFICATION_NOTICE,
            created_at=started + timedelta(minutes=7),
        ))
        session.add(Conversation(
            kb_id=1,
            question="unsupported",
            answer=UNSUPPORTED_ANSWER_NOTICE,
            created_at=started + timedelta(minutes=8),
        ))
        session.commit()

        history = _recent_usable_history(session, kb_id=1)

        assert [row.answer for row in history] == [
            "usable-6",
            "usable-5",
            "usable-4",
            "usable-3",
            "usable-2",
        ]
    finally:
        session.close()
