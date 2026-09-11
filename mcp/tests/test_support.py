from omarchy_hardware import server, support
from omarchy_hardware.config import Config
from omarchy_hardware.errors import UNSUPPORTED_OPERATION
from omarchy_hardware.server import list_capabilities


def test_export_includes_every_family():
    families = support.export()
    assert set(families) == set(support.FAMILIES) == set(support.MATRIX)
    assert families["raspberry_pi"][0]["id"] == "pi.inventory"
    assert "gpio.write" not in {row["id"] for row in families["jetson"]}


def test_unknown_family_is_unsupported():
    result = list_capabilities(family="toaster")
    assert result["ok"] is False
    assert result["error"]["code"] == UNSUPPORTED_OPERATION


def test_list_capabilities_filters_by_family():
    result = list_capabilities(family="jetson")
    assert result["ok"] is True
    assert list(result["families"]) == ["jetson"]
    assert all(row["availability"] == support.AVAIL_UNSUPPORTED for row in result["families"]["jetson"])


def test_serial_write_requires_confirmation_in_the_matrix():
    row = next(r for r in support.MATRIX["microcontroller"] if r["id"] == "serial.write")
    assert row["requires_confirmation"] is True
    assert row["safety"] == support.SAFETY_DESTRUCTIVE


def test_pwm_is_unsupported_on_raspberry_pi():
    error = support.unsupported("raspberry_pi", "gpio.pwm")
    assert error.code == UNSUPPORTED_OPERATION
    assert "gpio.pwm" in error.message
    assert "support-matrix.md" in error.hint


def test_capability_names_omit_unsupported_rows():
    names = support.capability_names("raspberry_pi")
    assert "gpio.write" in names
    assert "gpio.pwm" not in names
    assert "gpio.spi" not in names


def test_schneider_and_weintek_are_unsupported():
    for family in ("schneider", "weintek_hmi"):
        result = list_capabilities(family=family)
        assert result["ok"] is True
        rows = result["families"][family]
        assert rows
        assert all(row["availability"] == support.AVAIL_UNSUPPORTED for row in rows)
    weintek_ids = {row["id"] for row in support.export("weintek_hmi")["weintek_hmi"]}
    assert weintek_ids == {
        "hmi.identify",
        "opcua.read",
        "opcua.write",
        "mqtt.subscribe",
        "mqtt.publish",
    }
    schneider_ids = {row["id"] for row in support.export("schneider")["schneider"]}
    assert {"plc.discover", "plc.read", "plc.write"} <= schneider_ids


def test_hardware_report_is_redacted(monkeypatch):
    monkeypatch.setattr(server, "_config", lambda: Config(pi_hosts=("secret-pi.local",)))
    monkeypatch.setattr(server, "enumerate_boards", lambda: [{"port": "/dev/ttyUSB0"}])
    monkeypatch.setattr(server.sessions, "all", lambda: [{"session_id": "session-1"}])

    result = server.hardware_report()

    assert result["ok"] is True
    assert result["devices"] == [{"port": "/dev/ttyUSB0"}]
    assert result["sessions"] == [{"session_id": "session-1"}]
    assert result["remote_hosts_configured"] == 1
    assert "secret-pi.local" not in str(result)


def test_hardware_report_redacts_board_serial(monkeypatch):
    monkeypatch.setattr(server, "_config", lambda: Config())
    monkeypatch.setattr(
        server,
        "enumerate_boards",
        lambda: [{"port": "/dev/ttyACM0", "serial": "ABC123", "vid": "2341"}],
    )
    monkeypatch.setattr(server.sessions, "all", lambda: [])

    result = server.hardware_report()

    assert result["devices"][0]["serial"] == "redacted"
    assert "ABC123" not in str(result)
