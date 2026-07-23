from __future__ import annotations

from threading import Event, Thread

from agent.action_locks import action_lock


def test_same_thread_resolutions_are_serialized() -> None:
    first_entered = Event()
    release_first = Event()
    second_entered = Event()

    def first() -> None:
        with action_lock("thread-1"):
            first_entered.set()
            release_first.wait(timeout=2)

    def second() -> None:
        first_entered.wait(timeout=2)
        with action_lock("thread-1"):
            second_entered.set()

    first_thread = Thread(target=first)
    second_thread = Thread(target=second)
    first_thread.start()
    second_thread.start()

    assert first_entered.wait(timeout=2)
    assert not second_entered.wait(timeout=0.05)
    release_first.set()
    assert second_entered.wait(timeout=2)

    first_thread.join(timeout=2)
    second_thread.join(timeout=2)
    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
