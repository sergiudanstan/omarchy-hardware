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
from collections import deque
from collections.abc import Callable
from typing import Any

from . import errors
from .errors import ToolError

BUFFER_LIMIT = 256 * 1024
MAX_WAIT_MS = 10_000
READ_CHUNK = 4096
# serial_expect waits for a boot banner or a self-test line after a flash, which can
# take longer than an interactive read; still bounded.
MAX_EXPECT_MS = 30_000
MAX_LINE = 4096
CONTEXT_LINES = 20
CONTEXT_CHARS = 200


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
    def __init__(self, port: str, baud: int, write_timeout_ms: int = 2_000, **kwargs: Any) -> None:
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
        self._query_lock = threading.RLock()
        self._data_ready = threading.Condition(self._lock)
        self._closing = threading.Event()
        self._failed = threading.Event()
        self._clear_requested = 0
        self._clear_completed = 0
        self._clear_discarded = 0

        try:
            self._serial = serial.Serial(
                port=port, baudrate=baud, timeout=0.1, write_timeout=write_timeout_ms / 1000, **kwargs
            )
        except Exception as exc:
            raise ToolError(errors.SERIAL_ERROR, f"Could not open {port}: {exc}") from exc

        self._thread = threading.Thread(target=self._drain, name=f"serial-{port}", daemon=True)
        self._thread.start()

    def _drain(self) -> None:
        while not self._closing.is_set():
            try:
                chunk = self._serial.read(READ_CHUNK)
                with self._data_ready:
                    if self._clear_requested != self._clear_completed:
                        # Only the reader can discard bytes already inside read().
                        # Acknowledge after clearing the OS queue as well, before
                        # query() is allowed to send its next command.
                        self._clear_discarded = len(self._buffer) + len(chunk) + self._serial.in_waiting
                        self._serial.reset_input_buffer()
                        self._buffer.clear()
                        self._clear_completed = self._clear_requested
                        self._data_ready.notify_all()
                        continue
                    if chunk:
                        self._buffer.extend(chunk)
                        self._last_rx = time.time()
                        if len(self._buffer) > BUFFER_LIMIT:
                            excess = len(self._buffer) - BUFFER_LIMIT
                            del self._buffer[:excess]
                            self._dropped += excess
                        self._data_ready.notify_all()
            except Exception as exc:
                with self._data_ready:
                    self._errors.append(str(exc))
                    self._failed.set()
                    self._data_ready.notify_all()
                return

    def _require_open(self) -> None:
        if self._failed.is_set() or self._closing.is_set():
            raise ToolError(
                errors.SERIAL_ERROR,
                f"Serial session for {self.port} is closed or failed.",
                "Call serial_open to reconnect.",
            )

    def read(self, max_bytes: int, max_wait_ms: int, until: str | None) -> dict[str, Any]:
        wait_s = max(0, min(int(max_wait_ms), MAX_WAIT_MS)) / 1000.0
        deadline = time.monotonic() + wait_s
        # Ordinary reads must not steal an active query's reply. Include the
        # lock wait in the caller's deadline to retain the bounded-read contract.
        if not self._query_lock.acquire(timeout=wait_s):
            with self._data_ready:
                self._require_open()
                return self._take(0, timed_out=True)
        try:
            return self._read(max_bytes, deadline, until)
        finally:
            self._query_lock.release()

    def _read(self, max_bytes: int, deadline: float, until: str | None) -> dict[str, Any]:
        max_bytes = max(1, min(int(max_bytes), BUFFER_LIMIT))
        terminator = until.encode() if until else None
        with self._data_ready:
            while True:
                self._require_open()
                if terminator is not None:
                    index = self._buffer.find(terminator)
                    if index != -1:
                        end = min(index + len(terminator), max_bytes)
                        return self._take(end, timed_out=False)
                elif self._buffer:
                    return self._take(min(len(self._buffer), max_bytes), timed_out=False)

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    count = min(len(self._buffer), max_bytes)
                    return self._take(count, timed_out=True)
                self._data_ready.wait(remaining)

    def expect(self, matcher: Callable[[str], Any], max_wait_ms: int) -> dict[str, Any]:
        """Consume whole lines until matcher(line) is truthy or the deadline passes.

        Lines after the match stay buffered for the next read. A partial line is
        never consumed, except that a run of MAX_LINE bytes without a newline counts
        as a line so a device that never sends one cannot stall the scan.
        """
        wait_s = max(0, min(int(max_wait_ms), MAX_EXPECT_MS)) / 1000.0
        deadline = time.monotonic() + wait_s
        if not self._query_lock.acquire(timeout=wait_s):
            return {"matched": False, "timed_out": True, "lines_scanned": 0, "context": []}
        try:
            context: deque[str] = deque(maxlen=CONTEXT_LINES)
            scanned = 0
            with self._data_ready:
                while True:
                    self._require_open()
                    newline = self._buffer.find(b"\n", 0, MAX_LINE)
                    if newline != -1 or len(self._buffer) >= MAX_LINE:
                        end = newline + 1 if newline != -1 else MAX_LINE
                        raw = bytes(self._buffer[:end])
                        del self._buffer[:end]
                        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                        scanned += 1
                        found = matcher(line)
                        if found:
                            return {
                                "matched": True,
                                "timed_out": False,
                                "line": line[:MAX_LINE],
                                "match": found,
                                "lines_scanned": scanned,
                                "context": list(context),
                            }
                        context.append(line[:CONTEXT_CHARS])
                        continue

                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return {
                            "matched": False,
                            "timed_out": True,
                            "lines_scanned": scanned,
                            "context": list(context),
                            "partial_line_bytes": len(self._buffer),
                        }
                    self._data_ready.wait(remaining)
        finally:
            self._query_lock.release()

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
        with self._query_lock:
            self._require_open()
            try:
                # Do not flush()/tcdrain(): it can hang on stalled adapters.
                return int(self._serial.write(payload) or 0)
            except Exception as exc:
                raise ToolError(errors.SERIAL_ERROR, f"Write to {self.port} failed: {exc}") from exc

    def query(self, payload: bytes, max_wait_ms: int, until: str | None) -> tuple[int, dict[str, Any]]:
        with self._query_lock:
            discarded = self.clear()
            written = self.write(payload)
            result = self.read(4096, max_wait_ms, until)
            result["bytes_discarded_before_query"] = discarded
            return written, result

    def clear(self) -> int:
        with self._query_lock, self._data_ready:
            self._require_open()
            self._clear_requested += 1
            deadline = time.monotonic() + 2.0
            while self._clear_completed != self._clear_requested:
                self._require_open()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._failed.set()
                    self._data_ready.notify_all()
                    raise ToolError(errors.SERIAL_ERROR, f"Input reset for {self.port} timed out.")
                self._data_ready.wait(remaining)
            return self._clear_discarded

    def status(self) -> dict[str, Any]:
        with self._data_ready:
            return {
                "session_id": self.session_id,
                "port": self.port,
                "baud": self.baud,
                "open": not self._closing.is_set() and not self._failed.is_set() and self._serial.is_open,
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
                if existing.baud != baud:
                    raise ToolError(
                        errors.BAUD_MISMATCH,
                        f"{port} is already open at {existing.baud} baud, not {baud}.",
                        "Close the session first, or reopen at the same baud.",
                    )
                return existing
            stale = existing
        if stale is not None:
            stale.close()
        session = SerialSession(port, baud, **kwargs)
        with self._lock:
            current = self._sessions.get(port)
            if current is not None and current is not stale and current.status()["open"]:
                session.close()
                return current
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
