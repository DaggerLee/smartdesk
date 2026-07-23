from __future__ import annotations

from contextlib import contextmanager
from threading import Lock, RLock
from typing import Iterator


_registry_guard = Lock()
_entries: dict[str, tuple[RLock, int]] = {}


@contextmanager
def action_lock(thread_id: str) -> Iterator[None]:
    """Serialize one thread's complete resolve critical section."""
    with _registry_guard:
        lock, users = _entries.get(thread_id, (RLock(), 0))
        _entries[thread_id] = (lock, users + 1)

    lock.acquire()
    try:
        yield
    finally:
        lock.release()
        with _registry_guard:
            current_lock, users = _entries[thread_id]
            if users == 1:
                del _entries[thread_id]
            else:
                _entries[thread_id] = (current_lock, users - 1)
