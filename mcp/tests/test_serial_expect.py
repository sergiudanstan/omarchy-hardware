"""serial_expect against a stdlib PTY pair: whole lines, bounded waits, no regex."""

import os
import pty
import time

import pytest

from omarchy_hardware import errors, expect, server
from omarchy_hardware.errors import ToolError
from omarchy_hardware.serial_session import CONTEXT_LINES, MAX_EXPECT_MS, MAX_LINE, SerialSession


@pytest.fixture
def fake_port():
    master, slave = pty.openpty()
    session = SerialSession(os.ttyname(slave), 115200)
    try:
        yield master, session
    finally:
        session.close()
        os.close(master)
        os.close(slave)


def test_literal_match_consumes_up_to_the_line_and_keeps_the_rest(fake_port):
    master, session = fake_port
    os.write(master, b"boot\r\nwifi: connecting\r\nREADY v1.2\r\nnext line\r\n")
    result = session.expect(expect.build("literal", "READY"), 2000)
    assert result["matched"] is True
    assert result["line"] == "READY v1.2"
    assert result["context"] == ["boot", "wifi: connecting"]
    assert result["lines_scanned"] == 3
    assert session.read(4096, 1000, None)["data"] == b"next line\r\n"


def test_match_arriving_later_is_awaited(fake_port):
    master, session = fake_port
    os.write(master, b"starting\n")

    def late():
        time.sleep(0.3)
        os.write(master, b"selftest ok\n")

    import threading

    threading.Thread(target=late).start()
    result = session.expect(expect.build("prefix", "selftest"), 3000)
    assert result["matched"] is True and result["line"] == "selftest ok"


def test_silent_device_times_out_with_context(fake_port):
    master, session = fake_port
    os.write(master, b"a\nb\npartial")
    started = time.monotonic()
    result = session.expect(expect.build("literal", "never"), 300)
    assert 0.25 < time.monotonic() - started < 2.0
    assert result["matched"] is False and result["timed_out"] is True
    assert result["context"] == ["a", "b"]
    assert result["partial_line_bytes"] == len(b"partial")


def test_context_is_bounded(fake_port):
    master, session = fake_port
    os.write(master, b"".join(b"line %d %s\n" % (n, b"x" * 500) for n in range(CONTEXT_LINES + 10)))
    result = session.expect(expect.build("literal", "never"), 500)
    assert len(result["context"]) == CONTEXT_LINES
    assert all(len(line) <= 200 for line in result["context"])


def test_a_line_without_newline_cannot_stall_the_scan(fake_port):
    master, session = fake_port
    os.write(master, b"z" * (MAX_LINE + 10) + b"\nDONE\n")
    result = session.expect(expect.build("literal", "DONE"), 3000)
    assert result["matched"] is True
    assert result["lines_scanned"] == 3


def test_json_mode_requires_matching_keys_and_types(fake_port):
    master, session = fake_port
    os.write(master, b'noise {"selftest":"pass"}\n{"selftest":1}\n{"selftest":true,"t":21.5}\n')
    result = session.expect(expect.build("json", '{"selftest": true}'), 2000)
    assert result["matched"] is True
    assert result["match"] == {"json": {"selftest": True, "t": 21.5}}
    assert result["lines_scanned"] == 3


@pytest.mark.parametrize(
    ("mode", "match"),
    [
        ("regex", "READY.*"),
        ("literal", ""),
        ("prefix", ""),
        ("literal", "x" * 201),
        ("json", "[1, 2]"),
        ("json", "{not json"),
    ],
)
def test_bad_matchers_are_rejected(mode, match):
    with pytest.raises(ToolError) as caught:
        expect.build(mode, match)
    assert caught.value.code == errors.INVALID_ARGUMENT


def test_json_mode_with_empty_match_accepts_any_object_but_not_deep_nesting():
    matcher = expect.build("json", "")
    assert matcher("{}") == {"json": {}}
    assert matcher("[1]") is None
    assert matcher("{" * 5000) is None


def test_expect_tool_marks_output_untrusted_and_caps_the_wait(fake_port, monkeypatch):
    master, session = fake_port
    monkeypatch.setattr(server.sessions, "get", lambda session_id: session)
    os.write(master, b'{"fw":"blink","build":"abc"}\n')
    result = server.serial_expect("s", '{"fw": "blink"}', mode="json", max_wait_ms=10**9)
    assert result["ok"] is True and result["untrusted"] is True
    assert result["json"] == {"fw": "blink", "build": "abc"}
    assert "match" not in result
    assert MAX_EXPECT_MS == 30_000


def test_expect_tool_reports_bad_mode(monkeypatch):
    monkeypatch.setattr(server.sessions, "get", lambda session_id: None)
    assert server.serial_expect("s", "x", mode="regex")["error"]["code"] == errors.INVALID_ARGUMENT


def test_expect_on_a_closed_session_is_an_error(fake_port):
    _master, session = fake_port
    session.close()
    with pytest.raises(ToolError) as caught:
        session.expect(expect.build("literal", "x"), 200)
    assert caught.value.code == errors.SERIAL_ERROR
