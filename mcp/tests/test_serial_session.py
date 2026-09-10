"""Serial session tests against a stdlib PTY pair -- no board and no socat required."""

import os
import pty
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
    finally:
        manager.close_port(os.ttyname(slave))
        os.close(master)
        os.close(slave)


def test_manager_raises_for_unknown_session():
    from omarchy_hardware.errors import ToolError

    with pytest.raises(ToolError) as excinfo:
        SessionManager().get("deadbeefcafe")
    assert excinfo.value.code == "SESSION_NOT_FOUND"
