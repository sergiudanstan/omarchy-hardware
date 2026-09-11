from omarchy_hardware import server
from omarchy_hardware.config import Config
from omarchy_hardware.errors import UNCONFIRMED, UNKNOWN_ADAPTER
from omarchy_hardware.ids import identify
from omarchy_hardware.server import pi_status, serial_query, serial_write


def test_serial_write_requires_confirm(monkeypatch):
    monkeypatch.setattr(server, "_config", lambda: Config(allow_unknown_serial=True))
    monkeypatch.setattr(server.sessions, "get", lambda _sid: type("S", (), {"port": "/dev/ttyACM0"})())

    denied = serial_write("abc", "ping")
    assert denied["error"]["code"] == UNCONFIRMED


def test_serial_query_requires_confirm(monkeypatch):
    monkeypatch.setattr(server, "_config", lambda: Config(allow_unknown_serial=True))
    monkeypatch.setattr(server.sessions, "get", lambda _sid: type("S", (), {"port": "/dev/ttyACM0"})())

    denied = serial_query("abc", "ping")
    assert denied["error"]["code"] == UNCONFIRMED


def test_serial_write_refuses_unknown_adapter(monkeypatch):
    session = type("S", (), {"port": "/dev/ttyUSB0", "write": staticmethod(lambda _p: 1)})()
    monkeypatch.setattr(server, "_config", lambda: Config(allow_unknown_serial=False))
    monkeypatch.setattr(server.sessions, "get", lambda _sid: session)
    monkeypatch.setattr(
        server,
        "enumerate_boards",
        lambda: [{"port": "/dev/ttyUSB0", "board_type": "unknown"}],
    )

    denied = serial_write("abc", "ping", confirm=True)
    assert denied["error"]["code"] == UNKNOWN_ADAPTER


def test_gpio_errors_do_not_list_configured_hosts(monkeypatch):
    monkeypatch.setattr(
        server,
        "_config",
        lambda: Config(pi_hosts=("secret-pi.local", "other-pi.local")),
    )

    result = pi_status()
    assert result["ok"] is False
    assert "secret-pi.local" not in str(result)
    assert "other-pi.local" not in str(result)

    result = pi_status(host="evil.example.com")
    assert result["ok"] is False
    assert "secret-pi.local" not in str(result)


def test_ambiguous_espressif_pid_is_not_flashable():
    info = identify("303a", "1001")
    assert info.board_type == "unknown"
    assert info.fqbn is None
