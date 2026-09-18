import json

from omarchy_hardware import audit, panel_status
from omarchy_hardware.config import Config, ConfigError, MingMqttBroker, WeintekMqttTarget


def test_status_lists_configured_targets_and_verifies_the_log(monkeypatch, tmp_path):
    monkeypatch.setattr(audit, "STATE_DIR", tmp_path)
    audit._chain.reset()
    audit.record("serial_write", port="/dev/ttyACM0", bytes=1)
    monkeypatch.setattr(panel_status, "load_config", lambda: Config(
        pi_hosts=("lab-pi.local",),
        ming_allow=True,
        ming_mqtt=(MingMqttBroker(name="stack", host="127.0.0.1", port=1883),),
        weintek_mqtt=(WeintekMqttTarget("hmi.local", 8883, ("cMT/t",)),),
    ))

    result = panel_status.status()

    assert result["ok"] is True
    assert result["targets"]["pi"] == ["lab-pi.local"]
    assert result["targets"]["ming"]["mqtt"] == ["stack"]
    assert result["targets"]["weintek"] == {"allow": False, "opcua": 0, "mqtt": ["hmi.local:8883"], "modbus": []}
    assert result["audit"] == {"ok": True, "records": 1}
    # Topics, node ids and credentials stay out even of the user's own panel.
    assert "cMT/t" not in json.dumps(result)


def test_a_bad_config_is_reported_not_raised(monkeypatch, tmp_path):
    monkeypatch.setattr(audit, "STATE_DIR", tmp_path)

    def broken():
        raise ConfigError("pi.hosts must contain unique hostnames")

    monkeypatch.setattr(panel_status, "load_config", broken)

    result = panel_status.status()

    assert result["ok"] is False
    assert result["targets"] is None
    assert "pi.hosts" in result["config_error"]
    assert result["audit"]["ok"] is True
