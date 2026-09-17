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


def test_serial_write_is_refused_when_it_cannot_be_audited(monkeypatch):
    """An unauditable actuation is worse than a refused one."""
    written = []
    session = type("S", (), {"port": "/dev/ttyACM0", "write": staticmethod(written.append)})()
    monkeypatch.setattr(server, "_config", lambda: Config(allow_unknown_serial=True))
    monkeypatch.setattr(server.sessions, "get", lambda _sid: session)
    monkeypatch.setattr(server, "enumerate_boards", lambda: [])
    monkeypatch.setattr(
        server.audit, "record", lambda *_a, **_k: (_ for _ in ()).throw(OSError("read-only"))
    )

    refused = serial_write("abc", "ping", confirm=True)

    assert refused["error"]["code"] == "AUDIT_LOG_FAILED"
    assert written == []


def test_serial_write_records_a_digest_rather_than_the_payload(monkeypatch):
    records = []
    session = type("S", (), {"port": "/dev/ttyACM0", "write": staticmethod(lambda p: len(p))})()
    monkeypatch.setattr(server, "_config", lambda: Config(allow_unknown_serial=True))
    monkeypatch.setattr(server.sessions, "get", lambda _sid: session)
    monkeypatch.setattr(server, "enumerate_boards", lambda: [])
    monkeypatch.setattr(server.audit, "record", lambda event, **f: records.append((event, f)))

    assert serial_write("abc", "SET PUMP ON", confirm=True)["ok"] is True

    event, fields = records[0]
    assert event == "serial_write"
    assert fields["port"] == "/dev/ttyACM0"
    assert "SET PUMP ON" not in str(fields)


def test_gpio_writes_are_rate_limited_per_pin(monkeypatch):
    monkeypatch.setattr(server, "_config", lambda: Config(
        pi_hosts=("pi.local",), pi_allowed_pins=(17, 18), actuation_budget_per_min=2
    ))
    monkeypatch.setattr(server, "_actuation", None)
    monkeypatch.setattr(server.audit, "record", lambda *_a, **_k: None)
    monkeypatch.setattr(
        server.gpio_ssh, "write_pin", lambda *_a, **_k: {"previous_level": 0, "pin": {}}
    )

    assert server.gpio_write_pin(17, 1, host="pi.local", confirm=True)["ok"] is True
    assert server.gpio_write_pin(17, 0, host="pi.local", confirm=True)["ok"] is True

    throttled = server.gpio_write_pin(17, 1, host="pi.local", confirm=True)
    assert throttled["error"]["code"] == "RATE_LIMITED"

    # A different pin has its own budget.
    assert server.gpio_write_pin(18, 1, host="pi.local", confirm=True)["ok"] is True


def test_unexpected_errors_do_not_leak_their_message_to_the_model(monkeypatch):
    """hardware_report redacts serials; a stray traceback must not undo that."""
    monkeypatch.setattr(
        server,
        "enumerate_boards",
        lambda: (_ for _ in ()).throw(RuntimeError("/home/dan/secret-lab/pi.local failed")),
    )

    result = server.list_boards()

    assert result["ok"] is False
    assert result["error"]["code"] == "RuntimeError"
    assert "secret-lab" not in str(result)
