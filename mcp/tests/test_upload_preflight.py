"""Upload preflight: board identity, confirmation, and serial-session restore."""

from omarchy_hardware import server
from omarchy_hardware.config import Config
from omarchy_hardware.errors import ToolError
from omarchy_hardware.ids import identify

PORT = "/dev/ttyACM0"
FQBN = "arduino:avr:uno"
SKETCH = "/sketches/blink"

UNO = {
    "port": PORT,
    "vid": "2341",
    "pid": "0043",
    "serial": "ABC123",
    "board_type": "arduino_uno",
    "suggested_fqbn": FQBN,
}


def _patch_board(monkeypatch, boards, upload_result=None):
    monkeypatch.setattr(server, "_config", lambda: Config(allow_flash=True))
    monkeypatch.setattr(server, "_flash_budget", None)
    monkeypatch.setattr(server.policy, "resolve_port", lambda port: port)
    monkeypatch.setattr(server.policy, "check_readable", lambda port: None)
    monkeypatch.setattr(server, "enumerate_boards", lambda: boards)
    def fake_upload(*args, before_write=None, **kwargs):
        if before_write is not None:
            before_write()
        return upload_result or {"ok": True, "port": PORT, "fqbn": FQBN}

    monkeypatch.setattr(server.flash, "upload_sketch", fake_upload)
    monkeypatch.setattr(server.sessions, "by_port", lambda port: None)


def test_refuses_without_confirm(monkeypatch):
    _patch_board(monkeypatch, [UNO])

    result = server.upload_sketch(SKETCH, PORT, FQBN, "token", confirm=False)

    assert result["ok"] is False
    assert result["error"]["code"] == "FLASH_UNCONFIRMED"


def test_refuses_unknown_board(monkeypatch):
    unknown = {**UNO, "board_type": "unknown", "suggested_fqbn": None}
    _patch_board(monkeypatch, [unknown])

    result = server.upload_sketch(SKETCH, PORT, FQBN, "token", confirm=True)

    assert result["ok"] is False
    assert result["error"]["code"] == "UNKNOWN_BOARD"


def test_refuses_fqbn_mismatch_when_board_replaced(monkeypatch):
    mega = {**UNO, "board_type": "arduino_mega", "suggested_fqbn": "arduino:avr:mega"}
    _patch_board(monkeypatch, [mega])

    result = server.upload_sketch(SKETCH, PORT, FQBN, "token", confirm=True)

    assert result["ok"] is False
    assert result["error"]["code"] == "BOARD_MISMATCH"


def test_debug_probe_serial_port_is_not_a_pico_flash_target(monkeypatch):
    info = identify("2e8a", "000c")
    probe = {**UNO, "vid": "2e8a", "pid": "000c",
             "board_type": info.board_type, "suggested_fqbn": info.fqbn}
    _patch_board(monkeypatch, [probe])

    result = server.upload_sketch(SKETCH, PORT, "rp2040:rp2040:rpipico2", "token", confirm=True)

    assert result["ok"] is False
    assert result["error"]["code"] == "UNKNOWN_BOARD"


def test_refuses_disconnected_board(monkeypatch):
    _patch_board(monkeypatch, [])

    result = server.upload_sketch(SKETCH, PORT, FQBN, "token", confirm=True)

    assert result["ok"] is False
    assert result["error"]["code"] == "PORT_NOT_FOUND"


def test_matching_board_reaches_upload(monkeypatch):
    called = {}

    def fake_upload(sketch_dir, port, fqbn, token, **kwargs):
        called["args"] = (sketch_dir, port, fqbn, token)
        called["kwargs"] = kwargs
        return {"ok": True, "port": port, "fqbn": fqbn}

    _patch_board(monkeypatch, [UNO])
    monkeypatch.setattr(server.flash, "upload_sketch", fake_upload)

    result = server.upload_sketch(SKETCH, PORT, FQBN, "token", confirm=True)

    assert result["ok"] is True
    assert called["args"] == (SKETCH, PORT, FQBN, "token")
    assert called["kwargs"]["serial"] == "ABC123"


def test_refuses_when_flash_disabled(monkeypatch):
    monkeypatch.setattr(server, "_config", lambda: Config(allow_flash=False))

    result = server.upload_sketch(SKETCH, PORT, FQBN, "token", confirm=True)

    assert result["ok"] is False
    assert result["error"]["code"] == "FLASH_DISABLED"


def test_session_is_restored_after_upload(monkeypatch):
    class OpenSession:
        baud = 115200
        session_id = "old"

    class ReopenedSession(OpenSession):
        session_id = "new"

    opened: dict = {}
    _patch_board(monkeypatch, [UNO])
    monkeypatch.setattr(server, "_config", lambda: Config(allow_flash=True, write_timeout_ms=5_000))
    monkeypatch.setattr(server.sessions, "by_port", lambda port: OpenSession())
    monkeypatch.setattr(server.sessions, "close_port", lambda port: True)
    monkeypatch.setattr(
        server.sessions,
        "open",
        lambda port, baud, **kwargs: opened.update(port=port, baud=baud, **kwargs) or ReopenedSession(),
    )

    result = server.upload_sketch(SKETCH, PORT, FQBN, "token", confirm=True)

    assert result["ok"] is True
    assert result["session_restored"] is True
    # The reopened port keeps the configured write timeout, and the caller learns
    # the new session id instead of holding one that no longer exists.
    assert opened == {"port": PORT, "baud": 115200, "write_timeout_ms": 5_000}
    assert result["session_id"] == "new"


def test_session_restore_failure_is_reported_not_swallowed(monkeypatch):
    class OpenSession:
        baud = 115200

    _patch_board(monkeypatch, [UNO])
    monkeypatch.setattr(server.sessions, "by_port", lambda port: OpenSession())
    monkeypatch.setattr(server.sessions, "close_port", lambda port: True)

    def boom(port, baud, **_kwargs):
        raise ToolError("SERIAL_ERROR", f"Could not open {port}")

    monkeypatch.setattr(server.sessions, "open", boom)

    result = server.upload_sketch(SKETCH, PORT, FQBN, "token", confirm=True)

    assert result["ok"] is True
    assert result["session_restored"] is False
    assert result["session_restore_error"]["code"] == "SERIAL_ERROR"


def test_rate_limit_leaves_the_open_session_alone(monkeypatch):
    class OpenSession:
        baud = 115200
        session_id = "old"

    class ReopenedSession:
        baud = 115200
        session_id = "new"

    closed = []
    opened = []
    _patch_board(monkeypatch, [UNO])
    monkeypatch.setattr(server, "_config", lambda: Config(allow_flash=True, max_uploads_per_hour=1))
    monkeypatch.setattr(server.sessions, "by_port", lambda port: OpenSession())
    monkeypatch.setattr(server.sessions, "close_port", lambda port: closed.append(port))
    monkeypatch.setattr(
        server.sessions,
        "open",
        lambda *args, **kwargs: opened.append(args) or ReopenedSession(),
    )

    first = server.upload_sketch(SKETCH, PORT, FQBN, "token", confirm=True)
    assert first["ok"] is True
    assert first["session_restored"] is True
    assert first["session_id"] == "new"
    assert closed == [PORT]
    closed.clear()
    opened.clear()

    refused = server.upload_sketch(SKETCH, PORT, FQBN, "token", confirm=True)
    assert refused["error"]["code"] == "RATE_LIMITED"
    assert "session_id" not in refused
    assert closed == []
    assert opened == []


def test_upload_preflight_rejects_ambiguous_microbit(monkeypatch):
    microbit = {
        "port": PORT,
        "vid": "0d28",
        "pid": "0204",
        "serial": "ABC123",
        "board_type": "unknown",
        "suggested_fqbn": None,
    }
    _patch_board(monkeypatch, [microbit])

    result = server.upload_sketch(SKETCH, PORT, FQBN, "token", confirm=True)

    assert result["ok"] is False
    assert result["error"]["code"] == "UNKNOWN_BOARD"
