"""Serial session tests against a stdlib PTY pair -- no board and no socat required."""

import os
import pty
import select
import time

import pytest

from omarchy_hardware.serial_session import BUFFER_LIMIT, SerialSession, SessionManager


@pytest.fixture
def fake_port():
    """A PTY pair standing in for a board: writes to master surface on the slave."""
    master, slave = pty.openpty()
    session = SerialSession(os.ttyname(slave), 115200)
    try:
        yield master, session
    finally:
        session.close()
        os.close(master)
        os.close(slave)


def _settle(session, predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate(session.status()):
            return True
        time.sleep(0.01)
    return False


def test_reads_data_written_by_the_device(fake_port):
    master, session = fake_port
    os.write(master, b"hello board\n")

    result = session.read(max_bytes=4096, max_wait_ms=2000, until=None)

    assert b"hello board" in result["data"]
    assert result["timed_out"] is False


def test_silent_device_times_out_instead_of_hanging(fake_port):
    """The central guarantee: a device that never speaks costs max_wait_ms, not forever."""
    _master, session = fake_port

    started = time.monotonic()
    result = session.read(max_bytes=4096, max_wait_ms=300, until=None)
    elapsed = time.monotonic() - started

    assert result["data"] == b""
    assert result["timed_out"] is True
    assert 0.25 < elapsed < 2.0


def test_max_wait_is_clamped_to_ten_seconds(fake_port):
    _master, session = fake_port

    started = time.monotonic()
    session.read(max_bytes=10, max_wait_ms=10**9, until=None)
    elapsed = time.monotonic() - started

    assert elapsed < 12.0


def test_until_terminator_returns_one_line(fake_port):
    master, session = fake_port
    os.write(master, b"first\nsecond\n")

    result = session.read(max_bytes=4096, max_wait_ms=2000, until="\n")

    assert result["data"] == b"first\n"
    assert result["bytes_remaining"] > 0


def test_until_terminator_times_out_when_never_sent(fake_port):
    master, session = fake_port
    os.write(master, b"no terminator here")

    result = session.read(max_bytes=4096, max_wait_ms=300, until="\n")

    assert result["timed_out"] is True
    assert b"no terminator here" in result["data"]


def test_ring_buffer_drops_oldest_data_when_full(fake_port):
    master, session = fake_port
    payload = b"x" * 8192
    for _ in range((BUFFER_LIMIT // len(payload)) + 4):
        os.write(master, payload)

    assert _settle(session, lambda s: s["bytes_dropped"] > 0, timeout=5.0)

    status = session.status()
    assert status["bytes_buffered"] <= BUFFER_LIMIT


def test_clear_discards_buffered_data(fake_port):
    master, session = fake_port
    os.write(master, b"junk to discard\n")
    assert _settle(session, lambda s: s["bytes_buffered"] > 0)

    discarded = session.clear()

    assert discarded > 0
    assert session.status()["bytes_buffered"] == 0


def test_write_reaches_the_device(fake_port):
    master, session = fake_port
    written = session.write(b"ping\n")

    assert written == 5
    readable, _, _ = select.select([master], [], [], 2.0)
    assert readable, "serial write did not reach the PTY within 2 seconds"
    assert b"ping" in os.read(master, 1024)


def test_status_reports_open_session(fake_port):
    _master, session = fake_port
    status = session.status()

    assert status["open"] is True
    assert status["baud"] == 115200
    assert len(status["session_id"]) == 12


def test_manager_reuses_session_for_same_port():
    master, slave = pty.openpty()
    manager = SessionManager()
    try:
        port = os.ttyname(slave)
        first = manager.open(port, 115200)
        second = manager.open(port, 115200)

        assert first is second
        assert len(manager.all()) == 1

        from omarchy_hardware.errors import ToolError

        with pytest.raises(ToolError) as excinfo:
            manager.open(port, 9600)
        assert excinfo.value.code == "BAUD_MISMATCH"
        reopened = manager.open(port, 115200)
        assert reopened is first
    finally:
        manager.close_port(os.ttyname(slave))
        os.close(master)
        os.close(slave)


def test_manager_raises_for_unknown_session():
    from omarchy_hardware.errors import ToolError

    with pytest.raises(ToolError) as excinfo:
        SessionManager().get("deadbeefcafe")
    assert excinfo.value.code == "SESSION_NOT_FOUND"


def test_query_discards_bytes_held_inside_reader(monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace

    import serial

    from omarchy_hardware import serial_session

    held = threading.Event()
    release = threading.Event()
    clearing = threading.Event()

    class HeldRead(serial.Serial):
        def read(self, size=1):
            chunk = super().read(size)
            if b"STALE" in chunk:
                held.set()
                assert release.wait(2)
            return chunk

    monkeypatch.setattr(serial_session, "_require_pyserial", lambda: SimpleNamespace(Serial=HeldRead))
    master, slave = pty.openpty()
    session = SerialSession(os.ttyname(slave), 115200)
    original_clear = session.clear

    def clear():
        clearing.set()
        return original_clear()

    monkeypatch.setattr(session, "clear", clear)
    try:
        os.write(master, b"STALE\n")
        assert held.wait(2)
        with ThreadPoolExecutor(max_workers=1) as executor:
            response = executor.submit(session.query, b"new\n", 1000, "\n")
            assert clearing.wait(2)
            release.set()
            assert select.select([master], [], [], 2)[0]
            assert os.read(master, 1024) == b"new\n"
            os.write(master, b"FRESH\n")
            written, result = response.result(timeout=2)
        assert written == 4
        assert result["data"] == b"FRESH\n"
        assert result["bytes_discarded_before_query"] >= len(b"STALE\n")
    finally:
        release.set()
        session.close()
        os.close(master)
        os.close(slave)


def test_overlapping_queries_keep_replies_and_plain_read_deadline(fake_port):
    from concurrent.futures import ThreadPoolExecutor

    master, session = fake_port
    with ThreadPoolExecutor(max_workers=3) as executor:
        first = executor.submit(session.query, b"first\n", 2000, "\n")
        assert select.select([master], [], [], 2)[0]
        assert os.read(master, 1024) == b"first\n"
        second = executor.submit(session.query, b"second\n", 2000, "\n")
        plain = executor.submit(session.read, 4096, 100, "\n")
        assert plain.result(timeout=1)["timed_out"] is True
        assert not select.select([master], [], [], 0)[0]
        os.write(master, b"reply one\n")
        assert first.result(timeout=2)[1]["data"] == b"reply one\n"
        assert select.select([master], [], [], 2)[0]
        assert os.read(master, 1024) == b"second\n"
        os.write(master, b"reply two\n")
        assert second.result(timeout=2)[1]["data"] == b"reply two\n"


def test_stalled_write_times_out_and_recovers():
    from omarchy_hardware.errors import ToolError

    master, slave = pty.openpty()
    session = SerialSession(os.ttyname(slave), 115200, write_timeout_ms=100)
    try:
        started = time.monotonic()
        with pytest.raises(ToolError) as error:
            session.write(b"x" * (1024 * 1024))
        assert error.value.code == "SERIAL_ERROR"
        assert time.monotonic() - started < 2
        while select.select([master], [], [], 0.1)[0]:
            os.read(master, 65536)
        written = session.write(b"recovered\n")
        assert written == 10
        assert select.select([master], [], [], 2)[0]
        assert os.read(master, 1024) == b"recovered\n"
    finally:
        session.close()
        os.close(master)
        os.close(slave)


def test_disconnect_marks_failed_wakes_readers_and_reopens(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from omarchy_hardware.errors import ToolError

    master, slave = pty.openpty()
    replacement_master, replacement_slave = pty.openpty()
    port = tmp_path / "serial-port"
    port.symlink_to(os.ttyname(slave))
    manager = SessionManager()
    session = manager.open(str(port), 115200)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            reading = executor.submit(session.read, 100, 10000, "\n")
            os.close(master)
            with pytest.raises(ToolError) as error:
                reading.result(timeout=2)
            assert error.value.code == "SERIAL_ERROR"
        assert session.status()["open"] is False
        assert session.status()["errors"]
        port.unlink()
        port.symlink_to(os.ttyname(replacement_slave))
        reopened = manager.open(str(port), 115200)
        assert reopened is not session
        assert reopened.status()["open"] is True
        os.write(replacement_master, b"back\n")
        assert reopened.read(100, 1000, "\n")["data"] == b"back\n"
    finally:
        manager.close_port(str(port))
        os.close(slave)
        os.close(replacement_master)
        os.close(replacement_slave)


class _StubSession:
    def __init__(self, session_id, port, on_close=None):
        self.session_id, self.port, self._on_close = session_id, port, on_close

    def close(self):
        if self._on_close:
            self._on_close()

    def status(self):
        return {"open": True}


def test_closing_a_session_never_drops_the_one_that_replaced_it():
    manager = SessionManager()
    replacement = _StubSession("b", "/dev/ttyACM0")

    def reopened_meanwhile():
        # serial_open on the same port lands while the old session is closing.
        manager._sessions["/dev/ttyACM0"] = replacement

    manager._sessions["/dev/ttyACM0"] = _StubSession("a", "/dev/ttyACM0", on_close=reopened_meanwhile)
    manager.close("a")
    assert manager.by_port("/dev/ttyACM0") is replacement
