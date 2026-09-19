"""Per-board history: what was flashed to a physical board, and what the user calls it.

A board is recognised by its USB vendor id, product id and serial number, so the
same Uno is the same board on any port and after any reboot. Boards without a USB
serial number (most CH340 clones) cannot be told apart, and get no history rather
than a shared one.

The serial is device-controlled input. It is hashed into the file name and never
stored or used as a path. Labels are restricted to a short plain charset because
they are read back to the model later and must not be able to carry instructions.

Stdlib-only, like boards.py, so the bar widget can read labels before setup.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import threading
import time
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from . import errors
from .config import STATE_DIR
from .errors import ToolError

SCHEMA = 1
DIR_NAME = "boards"
MAX_UPLOADS = 20
MAX_FILE_BYTES = 256 * 1024
LABEL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}")

UNTRACKED_REASON = "The board reports no USB serial number, so its history cannot be told apart from other boards."

_lock = threading.Lock()


def journal_dir() -> Path:
    return STATE_DIR / DIR_NAME


def board_key(board: dict[str, Any]) -> str | None:
    serial = (board.get("serial") or "").strip()
    vid = (board.get("vid") or "").lower()
    pid = (board.get("pid") or "").lower()
    if not serial or not vid or not pid:
        return None
    return sha256(f"{vid}:{pid}:{serial}".encode()).hexdigest()[:32]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _empty(board: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "vid": (board.get("vid") or "").lower(),
        "pid": (board.get("pid") or "").lower(),
        "label": None,
        "first_recorded": _now(),
        "uploads": [],
    }


def _read(path: Path) -> dict[str, Any] | None:
    """The stored entry, or None if there is none. Raises ValueError if it is unreadable."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as handle:
        data = handle.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("journal entry is too large")
    entry = json.loads(data)
    if not isinstance(entry, dict) or entry.get("schema") != SCHEMA or not isinstance(entry.get("uploads"), list):
        raise ValueError("journal entry has an unexpected shape")
    return entry


def _write(path: Path, entry: dict[str, Any]) -> None:
    temp = path.with_suffix(f".tmp{os.getpid()}")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(entry, handle, indent=1, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def _update(board: dict[str, Any], change: Any) -> dict[str, Any]:
    """Apply change(entry) under an inter-process lock and store the result.

    Every Claude Code session runs its own MCP server, so two processes can record
    uploads at once; the lock file serialises the read-modify-write.
    """
    key = board_key(board)
    if key is None:
        raise ToolError(errors.JOURNAL_UNAVAILABLE, UNTRACKED_REASON)
    with _lock:
        directory = journal_dir()
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        path = directory / f"{key}.json"
        lock_fd = os.open(directory / ".lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(lock_fd, "w") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                entry = _read(path)
            except (ValueError, OSError):
                # Keep the damaged file for inspection instead of silently losing history.
                os.replace(path, path.with_suffix(f".corrupt-{int(time.time())}"))
                entry = None
            entry = entry or _empty(board)
            change(entry)
            _write(path, entry)
    return entry


def _public(entry: dict[str, Any] | None) -> dict[str, Any]:
    if entry is None:
        return {"tracked": True, "known": False, "label": None, "upload_count": 0, "uploads": []}
    uploads = list(reversed(entry["uploads"]))
    return {
        "tracked": True,
        "known": True,
        "label": entry.get("label"),
        "first_recorded": entry.get("first_recorded"),
        "upload_count": len(uploads),
        "last_upload": uploads[0] if uploads else None,
        "uploads": uploads,
    }


def history(board: dict[str, Any]) -> dict[str, Any]:
    """What is recorded about this physical board. Uploads are newest first."""
    key = board_key(board)
    if key is None:
        return {"tracked": False, "reason": UNTRACKED_REASON}
    try:
        entry = _read(journal_dir() / f"{key}.json")
    except (ValueError, OSError) as exc:
        raise ToolError(
            errors.JOURNAL_UNAVAILABLE,
            "The board's journal entry could not be read.",
            "It will be set aside and restarted on the next upload or label.",
        ) from exc
    return _public(entry)


def label(board: dict[str, Any]) -> str | None:
    """The user's name for the board, or None. Never raises: callers are best-effort."""
    try:
        result = history(board)
    except ToolError:
        return None
    return result.get("label")


def check_label(text: str) -> str | None:
    text = (text or "").strip()
    if not text:
        return None
    if not LABEL_PATTERN.fullmatch(text):
        raise ToolError(
            errors.INVALID_ARGUMENT,
            "A label is 1-40 characters: letters, digits, space, dot, underscore or hyphen.",
            "For example greenhouse-node or bench uno 2.",
        )
    return text


def set_label(board: dict[str, Any], text: str) -> dict[str, Any]:
    new_label = check_label(text)

    def change(entry: dict[str, Any]) -> None:
        entry["label"] = new_label

    return _public(_update(board, change))


def record_upload(board: dict[str, Any], *, fqbn: str, sketch_dir: str, artifact_digest: str) -> None:
    def change(entry: dict[str, Any]) -> None:
        entry["uploads"].append(
            {
                "at": _now(),
                "fqbn": fqbn,
                "sketch_dir": sketch_dir,
                "sketch": os.path.basename(sketch_dir.rstrip("/")),
                "artifact_digest": artifact_digest,
            }
        )
        del entry["uploads"][:-MAX_UPLOADS]

    _update(board, change)
