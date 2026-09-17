from omarchy_hardware.config import Config, WeintekMqttTarget, WeintekOpcUaTarget
from omarchy_hardware.errors import UNCONFIRMED, UNSUPPORTED_OPERATION
from omarchy_hardware.server import (
    gpio_set_mode,
    gpio_write_pin,
    weintek_mqtt_publish,
    weintek_opcua_read,
    weintek_opcua_write,
)

ENDPOINT = "opc.tcp://hmi.local:4840"
NODE = "ns=2;s=Temperature"


def _enabled(monkeypatch):
    monkeypatch.setattr(
        "omarchy_hardware.server._config",
        lambda: Config(
            weintek_allow=True,
            weintek_opcua=(WeintekOpcUaTarget(ENDPOINT, (NODE,)),),
            weintek_mqtt=(WeintekMqttTarget("hmi.local", 1883, ("cMT/temp",)),),
        ),
    )


def test_opcua_read_is_unsupported_for_listed_and_unlisted_nodes_alike(monkeypatch):
    # The allowlist is deliberately NOT consulted here: there is no client yet,
    # and answering differently for a listed node would tell a steered model
    # which endpoints exist. policy.check_weintek_opcua is covered in
    # test_policy.py, and belongs on this path only once it can act on it.
    _enabled(monkeypatch)
    listed = weintek_opcua_read(ENDPOINT, NODE)
    assert listed["ok"] is False
    assert listed["error"]["code"] == UNSUPPORTED_OPERATION

    unknown = weintek_opcua_read(ENDPOINT, "ns=2;s=other")
    assert unknown["error"]["code"] == UNSUPPORTED_OPERATION
    assert listed["error"] == unknown["error"]


def test_opcua_write_requires_confirm(monkeypatch):
    _enabled(monkeypatch)
    denied = weintek_opcua_write(ENDPOINT, NODE, "1")
    assert denied["error"]["code"] == UNCONFIRMED
    confirmed = weintek_opcua_write(ENDPOINT, NODE, "1", confirm=True)
    assert confirmed["error"]["code"] == UNSUPPORTED_OPERATION


def test_mqtt_publish_requires_confirm_and_stays_unsupported(monkeypatch):
    _enabled(monkeypatch)
    denied = weintek_mqtt_publish("hmi.local", "cMT/temp", "22.5")
    assert denied["error"]["code"] == UNCONFIRMED
    confirmed = weintek_mqtt_publish("hmi.local", "cMT/temp", "22.5", confirm=True)
    assert confirmed["error"]["code"] == UNSUPPORTED_OPERATION
    unknown = weintek_mqtt_publish("hmi.local", "cMT/other", "1", confirm=True)
    assert unknown["error"]["code"] == UNSUPPORTED_OPERATION


def test_gpio_writes_require_confirm(monkeypatch):
    monkeypatch.setattr(
        "omarchy_hardware.server._config",
        lambda: Config(pi_hosts=("pi.local",), pi_allowed_pins=(17,)),
    )
    denied_mode = gpio_set_mode(17, "out", host="pi.local")
    denied_write = gpio_write_pin(17, 1, host="pi.local")
    assert denied_mode["error"]["code"] == UNCONFIRMED
    assert denied_write["error"]["code"] == UNCONFIRMED
