from __future__ import annotations

import errno
import hashlib
import os
import re
import secrets
import stat
import unicodedata
from pathlib import Path

from agent.write_action import ActionReceipt, validate_write_note_payload


_ACTION_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")
_SLUG_SEPARATOR = re.compile(r"[^a-z0-9]+")


def _slugify(title: str) -> str:
    ascii_title = (
        unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    )
    slug = _SLUG_SEPARATOR.sub("-", ascii_title.lower()).strip("-")
    return (slug[:60].rstrip("-") or "note")


def _canonical_markdown(title: str, content: str) -> bytes:
    return f"# {title}\n\n{content}\n".encode("utf-8")


def _open_directory(parent_fd: int, name: str) -> int:
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
    except FileExistsError:
        pass
    return os.open(
        name,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        dir_fd=parent_fd,
    )


def _write_all(fd: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        offset += os.write(fd, payload[offset:])


def _read_regular_file(directory_fd: int, filename: str) -> bytes | None:
    metadata = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
    if not stat.S_ISREG(metadata.st_mode):
        return None
    fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
    try:
        chunks: list[bytes] = []
        while chunk := os.read(fd, 64 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


class WriteNoteTool:
    def __init__(self, user_id: int, action_id: str, notes_root: Path) -> None:
        if user_id <= 0:
            raise ValueError("user_id must be positive")
        if not _ACTION_ID_PATTERN.fullmatch(action_id):
            raise ValueError("action_id contains unsafe characters")
        self._user_id = user_id
        self._action_id = action_id
        self._notes_root = Path(notes_root)

    def run(self, title: str, content: str) -> ActionReceipt:
        payload = validate_write_note_payload(title, content)
        canonical = _canonical_markdown(payload.title, payload.content)
        content_hash = hashlib.sha256(canonical).hexdigest()
        filename = f"{_slugify(payload.title)}-{self._action_id}.md"
        relative_path = f"notes/{filename}"

        try:
            root_fd = os.open(
                self._notes_root,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
        except OSError:
            return self._failed("unsafe_path")

        users_fd = user_fd = notes_fd = None
        try:
            users_fd = _open_directory(root_fd, "users")
            user_fd = _open_directory(users_fd, str(self._user_id))
            notes_fd = _open_directory(user_fd, "notes")
            return self._publish(
                notes_fd,
                filename,
                relative_path,
                canonical,
                content_hash,
            )
        except OSError as exc:
            error_code = "unsafe_path" if exc.errno in {errno.ELOOP, errno.ENOTDIR} else "write_failed"
            return self._failed(error_code)
        finally:
            for fd in (notes_fd, user_fd, users_fd, root_fd):
                if fd is not None:
                    os.close(fd)

    def _publish(
        self,
        notes_fd: int,
        filename: str,
        relative_path: str,
        canonical: bytes,
        content_hash: str,
    ) -> ActionReceipt:
        try:
            existing = _read_regular_file(notes_fd, filename)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            return self._existing_receipt(existing, canonical, relative_path, content_hash)
        try:
            os.stat(filename, dir_fd=notes_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            return self._conflict()

        temporary = f".write-note-{self._action_id}-{secrets.token_hex(8)}.tmp"
        temp_fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=notes_fd,
        )
        published = False
        try:
            _write_all(temp_fd, canonical)
            os.fsync(temp_fd)
            try:
                os.link(
                    temporary,
                    filename,
                    src_dir_fd=notes_fd,
                    dst_dir_fd=notes_fd,
                    follow_symlinks=False,
                )
                published = True
            except FileExistsError:
                try:
                    existing = _read_regular_file(notes_fd, filename)
                except OSError:
                    return self._conflict()
                if existing is None:
                    return self._conflict()
                return self._existing_receipt(
                    existing, canonical, relative_path, content_hash
                )
            os.fsync(notes_fd)
        finally:
            os.close(temp_fd)
            try:
                os.unlink(temporary, dir_fd=notes_fd)
                os.fsync(notes_fd)
            except FileNotFoundError:
                pass

        if not published:
            return self._failed("write_failed")
        read_back = _read_regular_file(notes_fd, filename)
        if read_back != canonical:
            return self._failed("read_back_mismatch")
        return ActionReceipt(
            action_id=self._action_id,
            result="succeeded",
            relative_path=relative_path,
            content_hash=content_hash,
            byte_count=len(canonical),
            read_back_verified=True,
        )

    def _existing_receipt(
        self,
        existing: bytes,
        canonical: bytes,
        relative_path: str,
        content_hash: str,
    ) -> ActionReceipt:
        if existing != canonical:
            return self._conflict()
        return ActionReceipt(
            action_id=self._action_id,
            result="replayed",
            relative_path=relative_path,
            content_hash=content_hash,
            byte_count=len(canonical),
            read_back_verified=True,
        )

    def _conflict(self) -> ActionReceipt:
        return ActionReceipt(
            action_id=self._action_id,
            result="conflict",
            error_code="existing_file_conflict",
        )

    def _failed(self, error_code: str) -> ActionReceipt:
        return ActionReceipt(
            action_id=self._action_id,
            result="failed",
            error_code=error_code,
        )
