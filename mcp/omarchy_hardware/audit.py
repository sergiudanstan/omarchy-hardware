"""Tamper-evident audit log for every operation that changes physical state.

Flashing already refused to start without a writable log. The same guarantee now
covers serial writes, GPIO changes, and any actuation added later, because the
first question after an incident on real equipment is what moved, when, and with
what value.

Records are chained: each line carries the SHA-256 of the line before it, so
deleting or editing history breaks verification. The file is owned by the user,
and a process running as that user can still rewrite it -- but not undetectably,
and `verify()` names the first line where the chain stops following.

Payload *contents* are deliberately not stored. A serial write records its length
and a SHA-256 of the bytes, which is enough to confirm or refute "this exact
command was sent" without turning the log into a plaintext record of everything
the user's devices ever received.
"""

from __future__ import annotations

import json
import os
import threading
import time
from hashlib import sha256
from pathlib import Path
from typing import Any

from . import errors
from .config import STATE_DIR
from .errors import ToolError

LOG_NAME = "audit.log"
GENESIS = "0" * 64
_DUMP = {"separators": (",", ":"), "sort_keys": True}

_lock = threading.Lock()


class _Chain:
    """The tail of the log, cached so appending does not re-read the whole file.

    Held as state rather than loose module globals so the cache and the path it
    belongs to cannot drift apart: pointing STATE_DIR somewhere else invalidates
    the head automatically instead of chaining a new file onto an old hash.
    """

    def __init__(self) -> None:
        self.head: str | None = None
        self.path: Path | None = None

    def tail(self, path: Path) -> str:
        if self.head is None or self.path != path:
            self.head = _read_head(path)
            self.path = path
        return self.head

    def advance(self, digest: str) -> None:
        self.head = digest

    def reset(self) -> None:
        """Forget the cached head, as a fresh process would."""
        self.head = None
        self.path = None


_chain = _Chain()


def log_path() -> Path:
    return STATE_DIR / LOG_NAME


def _line_hash(previous: str, body: str) -> str:
    return sha256(f"{previous}\n{body}".encode()).hexdigest()


def _read_head(path: Path) -> str:
    """Recover the chain head from disk so a restart continues the same chain."""
    head = GENESIS
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    head = json.loads(stripped)["hash"]
                except (ValueError, KeyError, TypeError):
                    # A truncated or edited tail must not silently restart the
                    # chain: keep the last good hash so verify() still reports
                    # the damage rather than accepting a fresh genesis.
                    continue
    except FileNotFoundError:
        return GENESIS
    return head if isinstance(head, str) else GENESIS


def record(event: str, **fields: Any) -> str:
    """Append one chained record. Raises OSError if the log cannot be written."""
    with _lock:
        path = log_path()
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(STATE_DIR, 0o700)

        previous = _chain.tail(path)
        payload = {"at": time.time(), "event": event, "prev": previous, **fields}
        digest = _line_hash(previous, json.dumps(payload, **_DUMP))

        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            os.chmod(path, 0o600)
            handle.write(json.dumps({**payload, "hash": digest}, **_DUMP) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

        _chain.advance(digest)
        return digest


def require(event: str, action: str, **fields: Any) -> None:
    """Record, or refuse the operation outright.

    Called before anything that moves hardware: an unauditable actuation is worse
    than a refused one.
    """
    try:
        record(event, **fields)
    except OSError as exc:
        raise ToolError(
            errors.AUDIT_LOG_FAILED,
            f"Refusing to {action} because the audit log is unavailable.",
            f"Fix permissions for {log_path()} and try again.",
        ) from exc


def note(event: str, **fields: Any) -> None:
    """Record the outcome of an operation that has already happened.

    The actuation cannot be undone at this point, so a failure here is reported
    by the caller rather than raised over the result the user needs to see.
    """
    record(event, **fields)


def payload_digest(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def verify(path: Path | None = None) -> dict[str, Any]:
    """Walk the chain and report the first record that does not follow."""
    target = path or log_path()
    previous = GENESIS
    count = 0

    try:
        handle = target.open("r", encoding="utf-8")
    except FileNotFoundError:
        return {"ok": True, "records": 0, "path": str(target)}

    with handle:
        for number, raw in enumerate(handle, start=1):
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
                digest = entry.pop("hash")
            except (ValueError, KeyError, AttributeError):
                return _broken(target, count, number, "record is not a chained JSON object")
            if entry.get("prev") != previous or _line_hash(previous, json.dumps(entry, **_DUMP)) != digest:
                return _broken(target, count, number, "record does not follow the one before it")
            previous = digest
            count += 1

    return {"ok": True, "records": count, "path": str(target)}


def _broken(target: Path, count: int, line: int, reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "records": count,
        "broken_at_line": line,
        "reason": reason,
        "path": str(target),
    }
