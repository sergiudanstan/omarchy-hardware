import json
import socket

import pytest
from ming_fakes import FakeBroker, wait_for

from omarchy_hardware import audit, server
from omarchy_hardware.config import Config, MqttSecurity, WeintekMqttTarget, WeintekOpcUaTarget
from omarchy_hardware.errors import (
    HOST_NOT_ALLOWED,
    INSECURE_TRANSPORT,
    INVALID_ARGUMENT,
    RATE_LIMITED,
    SERVICE_UNREACHABLE,
    UNCONFIRMED,
    UNSUPPORTED_OPERATION,
    WRITE_TOO_LARGE,
)
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


def test_mqtt_publish_requires_confirm_and_an_allowlisted_topic(monkeypatch):
    _enabled(monkeypatch)
    denied = weintek_mqtt_publish("hmi.local", "cMT/temp", "22.5", port=1883)
    assert denied["error"]["code"] == UNCONFIRMED
    unknown = weintek_mqtt_publish("hmi.local", "cMT/other", "1", port=1883, confirm=True)
    assert unknown["error"]["code"] == HOST_NOT_ALLOWED
    # The TLS default port does not match an entry configured on 1883.
    wrong_port = weintek_mqtt_publish("hmi.local", "cMT/temp", "1", confirm=True)
    assert wrong_port["error"]["code"] == HOST_NOT_ALLOWED


@pytest.fixture
def hmi(monkeypatch, tmp_path):
    monkeypatch.setattr(audit, "STATE_DIR", tmp_path / "state")
    audit._chain.reset()
    monkeypatch.setattr(server, "_actuation", None)
    broker = FakeBroker()
    target = WeintekMqttTarget(
        "127.0.0.1", broker.port, ("cMT/setpoint",), MqttSecurity(tls=False, allow_insecure=True)
    )
    monkeypatch.setattr(
        server, "_config", lambda: Config(weintek_allow=True, weintek_mqtt=(target,), actuation_budget_per_min=2)
    )
    yield broker
    broker.close()


def _events():
    return [json.loads(line)["event"] for line in audit.log_path().read_text().splitlines()]


def test_mqtt_publish_reaches_the_broker_and_is_audited(hmi):
    sent = weintek_mqtt_publish("127.0.0.1", "cMT/setpoint", "21.5", port=hmi.port, qos=1, confirm=True)

    assert sent["ok"] is True, sent
    assert wait_for(lambda: hmi.published)
    assert hmi.published[0] == {"topic": "cMT/setpoint", "payload": b"21.5", "qos": 1, "retain": False}
    assert _events() == ["weintek_mqtt_publish", "weintek_mqtt_publish_done"]
    assert "21.5" not in audit.log_path().read_text()
    assert audit.verify()["ok"] is True


def test_mqtt_publish_is_rate_limited_per_topic(hmi):
    for _ in range(2):
        assert weintek_mqtt_publish("127.0.0.1", "cMT/setpoint", "1", port=hmi.port, confirm=True)["ok"] is True
    throttled = weintek_mqtt_publish("127.0.0.1", "cMT/setpoint", "1", port=hmi.port, confirm=True)
    assert throttled["error"]["code"] == RATE_LIMITED


def test_mqtt_publish_refuses_oversized_payloads_and_bad_qos(hmi):
    big = weintek_mqtt_publish("127.0.0.1", "cMT/setpoint", "x" * 4097, port=hmi.port, confirm=True)
    assert big["error"]["code"] == WRITE_TOO_LARGE
    qos2 = weintek_mqtt_publish("127.0.0.1", "cMT/setpoint", "1", port=hmi.port, qos=2, confirm=True)
    assert qos2["error"]["code"] == INVALID_ARGUMENT
    assert hmi.published == []


def test_unreachable_broker_is_recorded_as_failed(monkeypatch, hmi):
    # A port that was free a moment ago: nothing is listening on it.
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    target = WeintekMqttTarget("127.0.0.1", port, ("cMT/setpoint",), MqttSecurity(tls=False, allow_insecure=True))
    monkeypatch.setattr(server, "_config", lambda: Config(weintek_allow=True, weintek_mqtt=(target,)))

    failed = weintek_mqtt_publish("127.0.0.1", "cMT/setpoint", "1", port=port, confirm=True)
    assert failed["error"]["code"] == SERVICE_UNREACHABLE
    assert _events() == ["weintek_mqtt_publish", "weintek_mqtt_publish_failed"]


def test_cleartext_target_without_waiver_is_refused(monkeypatch):
    target = WeintekMqttTarget("hmi.local", 1883, ("cMT/temp",), MqttSecurity(tls=False))
    monkeypatch.setattr(server, "_config", lambda: Config(weintek_allow=True, weintek_mqtt=(target,)))
    refused = weintek_mqtt_publish("hmi.local", "cMT/temp", "1", port=1883, confirm=True)
    assert refused["error"]["code"] == INSECURE_TRANSPORT


def test_gpio_writes_require_confirm(monkeypatch):
    monkeypatch.setattr(
        "omarchy_hardware.server._config",
        lambda: Config(pi_hosts=("pi.local",), pi_allowed_pins=(17,)),
    )
    denied_mode = gpio_set_mode(17, "out", host="pi.local")
    denied_write = gpio_write_pin(17, 1, host="pi.local")
    assert denied_mode["error"]["code"] == UNCONFIRMED
    assert denied_write["error"]["code"] == UNCONFIRMED
