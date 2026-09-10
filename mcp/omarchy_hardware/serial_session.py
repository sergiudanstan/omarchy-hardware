"""Long-lived serial sessions owned by the server process.

MCP tool calls are stateless, so a `Serial` object cannot survive between them. The
server keeps the port open and a background thread drains it into a bounded ring
buffer. Every read is served from that buffer under a deadline, so a device that
never sends anything can only ever cost the caller its `max_wait_ms` -- it can
never block indefinitely.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from . import errors
from .errors import ToolError

BUFFER_LIMIT = 256 * 1024
MAX_WAIT_MS = 10_000
READ_CHUNK = 4096


def _require_pyserial():
    try:
        import serial  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - exercised only without the venv
        raise ToolError(
            errors.TOOL_MISSING,
            "pyserial is not installed.",
            "Run the plugin's bin/setup.sh to create the virtualenv.",
        ) from exc
    return serial


class SerialSession:
    def __init__(self, port: str, baud: int, **kwargs: Any) -> None:
        serial = _require_pyserial()

        self.session_id = uuid.uuid4().hex[:12]
        self.port = port
        self.baud = baud
        self.opened_at = time.time()

        self._buffer = bytearray()
        self._dropped = 0
        self._last_rx: float | None = None
        self._errors: list[str] = []
        self._lock = threading.Lock()
        self._data_ready = threading.Condition(self._lock)
        self._closing = threading.Event()

        try:
            self._serial = serial.Serial(port=port, baudrate=baud, timeout=0.1, **kwargs)
        except Exception as exc:
            raise ToolError(errors.SERIAL_ERROR, f"Could not open {port}: {exc}") from exc

        self._thread = threading.Thread(target=self._drain, name=f"serial-{port}", daemon=True)
        self._thread.start()

    def _drain(self) -> None:
        while not self._closing.is_set():
            try:
                chunk = self._serial.read(READ_CHUNK)
            except Exception as exc:
                with self._data_ready:
                    self._errors.append(str(exc))
                    self._data_ready.notify_all()
                return

            if not chunk:
                continue

            with self._data_ready:
                self._buffer.extend(chunk)
                self._last_rx = time.time()
                if len(self._buffer) > BUFFER_LIMIT:
                    excess = len(self._buffer) - BUFFER_LIMIT
                    del self._buffer[:excess]
                    self._dropped += excess
                self._data_ready.notify_all()

    def read(self, max_bytes: int, max_wait_ms: int, until: str | None) -> dict[str, Any]:
        max_bytes = max(1, min(int(max_bytes), BUFFER_LIMIT))
        wait_s = max(0, min(int(max_wait_ms), MAX_WAIT_MS)) / 1000.0
        terminator = until.encode() if until else None
        deadline = time.monotonic() + wait_s

        with self._data_ready:
            while True:
                if terminator is not None:
                    index = self._buffer.find(terminator)
                    if index != -1:
                        end = min(index + len(terminator), max_bytes)
                        return self._take(end, timed_out=False)
                elif self._buffer:
                    return self._take(min(len(self._buffer), max_bytes), timed_out=False)

                remaining = deadline - time.monotonic()
                if remaining <= 0 or self._closing.is_set():
                    # Hand back whatever arrived rather than nothing, and say so.
                    count = min(len(self._buffer), max_bytes)
                    return self._take(count, timed_out=True)

                self._data_ready.wait(remaining)

    def _take(self, count: int, timed_out: bool) -> dict[str, Any]:
        data = bytes(self._buffer[:count])
        del self._buffer[:count]
        return {
            "data": data,
            "timed_out": timed_out,
            "bytes_remaining": len(self._buffer),
            "bytes_dropped": self._dropped,
        }

    def write(self, payload: bytes) -> int:
        try:
            written = self._serial.write(payload)
            self._serial.flush()
            return int(written or 0)
        except Exception as exc:
            raise ToolError(errors.SERIAL_ERROR, f"Write to {self.port} failed: {exc}") from exc

    def clear(self) -> int:
        with self._data_ready:
            discarded = len(self._buffer)
            self._buffer.clear()
            return discarded

    def status(self) -> dict[str, Any]:
        with self._data_ready:
            return {
                "session_id": self.session_id,
                "port": self.port,
                "baud": self.baud,
                "open": not self._closing.is_set() and self._serial.is_open,
                "bytes_buffered": len(self._buffer),
                "bytes_dropped": self._dropped,
                "last_rx_at": self._last_rx,
                "opened_at": self.opened_at,
                "errors": list(self._errors),
            }

    def close(self) -> None:
        self._closing.set()
        with self._data_ready:
            self._data_ready.notify_all()
        self._thread.join(timeout=2.0)
        try:
            self._serial.close()
        except Exception as exc:
            # Record rather than swallow: a port that failed to close is why the
            # next serial_open on it may report PORT_BUSY.
            with self._data_ready:
                self._errors.append(f"close failed: {exc}")


class SessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, SerialSession] = {}
        self._lock = threading.Lock()

    def open(self, port: str, baud: int, **kwargs: Any) -> SerialSession:
        with self._lock:
            existing = self._sessions.get(port)
            if existing is not None and existing.status()["open"]:
                return existing
            session = SerialSession(port, baud, **kwargs)
            self._sessions[port] = session
            return session

    def get(self, session_id: str) -> SerialSession:
        with self._lock:
            for session in self._sessions.values():
                if session.session_id == session_id:
                    return session
        raise ToolError(
            errors.SESSION_NOT_FOUND,
            f"No open session {session_id!r}.",
            "Call serial_open first, or list_sessions to see what is open.",
        )

    def by_port(self, port: str) -> SerialSession | None:
        with self._lock:
            return self._sessions.get(port)

    def close(self, session_id: str) -> None:
        session = self.get(session_id)
        session.close()
        with self._lock:
            self._sessions.pop(session.port, None)

    def close_port(self, port: str) -> bool:
        with self._lock:
            session = self._sessions.pop(port, None)
        if session is None:
            return False
        session.close()
        return True

    def all(self) -> list[dict[str, Any]]:
        with self._lock:
            return [session.status() for session in self._sessions.values()]
