"""Upload preflight: board identity, confirmation, and serial-session restore."""

from omarchy_hardware import server
from omarchy_hardware.config import Config
from omarchy_hardware.errors import ToolError

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
    monkeypatch.setattr(server.policy, "resolve_port", lambda port: port)
    monkeypatch.setattr(server.policy, "check_readable", lambda port: None)
    monkeypatch.setattr(server, "enumerate_boards", lambda: boards)
    monkeypatch.setattr(
        server.flash,
        "upload_sketch",
        lambda *args, **kwargs: upload_result or {"ok": True, "port": PORT, "fqbn": FQBN},
    )
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

    opened: dict = {}
    _patch_board(monkeypatch, [UNO])
    monkeypatch.setattr(server.sessions, "by_port", lambda port: OpenSession())
    monkeypatch.setattr(server.sessions, "close_port", lambda port: True)
    monkeypatch.setattr(
        server.sessions,
        "open",
        lambda port, baud: opened.update(port=port, baud=baud) or OpenSession(),
    )

    result = server.upload_sketch(SKETCH, PORT, FQBN, "token", confirm=True)

    assert result["ok"] is True
    assert result["session_restored"] is True
    assert opened == {"port": PORT, "baud": 115200}


def test_session_restore_failure_is_reported_not_swallowed(monkeypatch):
    class OpenSession:
        baud = 115200

    _patch_board(monkeypatch, [UNO])
    monkeypatch.setattr(server.sessions, "by_port", lambda port: OpenSession())
    monkeypatch.setattr(server.sessions, "close_port", lambda port: True)

    def boom(port, baud):
        raise ToolError("SERIAL_ERROR", f"Could not open {port}")

    monkeypatch.setattr(server.sessions, "open", boom)

    result = server.upload_sketch(SKETCH, PORT, FQBN, "token", confirm=True)

    assert result["ok"] is True
    assert result["session_restored"] is False
    assert result["session_restore_error"]["code"] == "SERIAL_ERROR"


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
