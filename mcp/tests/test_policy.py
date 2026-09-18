import os

import pytest

from omarchy_hardware import policy
from omarchy_hardware.config import (
    Config,
    MqttSecurity,
    OpcUaSecurity,
    WeintekMqttTarget,
    WeintekOpcUaTarget,
)
from omarchy_hardware.errors import ToolError


@pytest.mark.parametrize(
    "port",
    [
        "/dev/sda",
        "/etc/passwd",
        "/dev/ttyS0",  # built-in UART, often a serial console
        "../../dev/sda",
        "/dev/serial/by-id/../../sda",
        "/dev/ttyACM0extra",
        "",
        "/dev/ttyACM0\x00/dev/sda",
    ],
)
def test_resolve_port_rejects_paths_outside_the_allowlist(port):
    with pytest.raises(ToolError) as excinfo:
        policy.resolve_port(port)
    assert excinfo.value.code in {"PORT_NOT_ALLOWED", "PORT_NOT_FOUND"}


def test_resolve_port_rejects_symlink_escape(monkeypatch, tmp_path):
    """An allowlisted-looking path that resolves elsewhere must still be refused."""
    monkeypatch.setattr(os.path, "realpath", lambda _p: "/dev/sda")

    with pytest.raises(ToolError) as excinfo:
        policy.resolve_port("/dev/ttyACM0")

    assert excinfo.value.code == "PORT_NOT_ALLOWED"
    assert "/dev/sda" in excinfo.value.message


def test_resolve_port_reports_missing_device_separately(monkeypatch):
    monkeypatch.setattr(os.path, "realpath", lambda p: p)

    with pytest.raises(ToolError) as excinfo:
        policy.resolve_port("/dev/ttyACM99")

    assert excinfo.value.code == "PORT_NOT_FOUND"


def test_check_host_requires_configuration():
    with pytest.raises(ToolError) as excinfo:
        policy.check_host("pi.local", Config())
    assert excinfo.value.code == "HOST_NOT_ALLOWED"


def test_check_host_rejects_unlisted_host():
    config = Config(pi_hosts=("secret-pi.local",))
    assert policy.check_host("secret-pi.local", config) == "secret-pi.local"

    with pytest.raises(ToolError) as excinfo:
        policy.check_host("evil.example.com", config)
    assert excinfo.value.code == "HOST_NOT_ALLOWED"
    assert "secret-pi.local" not in excinfo.value.message
    assert "secret-pi.local" not in excinfo.value.hint


def test_check_pin_enforces_allowlist():
    config = Config(pi_allowed_pins=(17, 18))
    assert policy.check_pin(17, config) == 17

    for bad in (0, 1, 99, -1):
        with pytest.raises(ToolError):
            policy.check_pin(bad, config)


def test_check_pin_rejects_bool_masquerading_as_int():
    # bool is a subclass of int; True must not silently become pin 1.
    with pytest.raises(ToolError):
        policy.check_pin(True, Config(pi_allowed_pins=(1,)))


SECURE_OPCUA = OpcUaSecurity(certificate="/etc/pki/client.der", private_key="/etc/pki/client.key")


def test_weintek_opcua_requires_allow_and_exact_node():
    config = Config(
        weintek_allow=True,
        weintek_opcua=(WeintekOpcUaTarget("opc.tcp://hmi.local:4840", ("ns=2;s=T",), SECURE_OPCUA),),
    )
    policy.check_weintek_opcua(config, "opc.tcp://hmi.local:4840", "ns=2;s=T")
    with pytest.raises(ToolError) as excinfo:
        policy.check_weintek_opcua(config, "opc.tcp://hmi.local:4840", "ns=2;s=other")
    assert excinfo.value.code == "HOST_NOT_ALLOWED"
    with pytest.raises(ToolError):
        policy.check_weintek_opcua(Config(), "opc.tcp://hmi.local:4840", "ns=2;s=T")


def test_weintek_mqtt_rejects_unlisted_topic():
    config = Config(
        weintek_allow=True,
        weintek_mqtt=(WeintekMqttTarget("hmi.local", 1883, ("cMT/temp",)),),
    )
    policy.check_weintek_mqtt(config, "hmi.local", "cMT/temp", port=1883)
    with pytest.raises(ToolError) as excinfo:
        policy.check_weintek_mqtt(config, "hmi.local", "cMT/other", port=1883)
    assert excinfo.value.code == "HOST_NOT_ALLOWED"


def test_write_budget_enforces_rolling_cap():
    budget = policy.WriteBudget(100)
    budget.charge("/dev/ttyACM0", 60)
    budget.charge("/dev/ttyACM0", 40)

    with pytest.raises(ToolError) as excinfo:
        budget.charge("/dev/ttyACM0", 1)
    assert excinfo.value.code == "RATE_LIMITED"

    # Budget is per port, so a different device is unaffected.
    budget.charge("/dev/ttyUSB0", 100)


def test_actuation_budget_counts_operations_not_bytes():
    budget = policy.ActuationBudget(3)
    for _ in range(3):
        budget.charge("pi.local:17")

    with pytest.raises(ToolError) as excinfo:
        budget.charge("pi.local:17")
    assert excinfo.value.code == "RATE_LIMITED"

    # Per host and pin, so one runaway pin cannot starve the rest of the board.
    budget.charge("pi.local:18")
    budget.charge("other.local:17")


def test_opcua_refuses_an_endpoint_with_no_transport_security():
    bare = OpcUaSecurity(policy="None", mode="None")
    config = Config(
        weintek_allow=True,
        weintek_opcua=(WeintekOpcUaTarget("opc.tcp://hmi.local:4840", ("ns=2;s=T",), bare),),
    )

    with pytest.raises(ToolError) as excinfo:
        policy.check_weintek_opcua(config, "opc.tcp://hmi.local:4840", "ns=2;s=T")
    assert excinfo.value.code == "INSECURE_TRANSPORT"


def test_opcua_returns_the_security_context_to_connect_with():
    """An authorised endpoint must arrive with the settings to reach it safely."""
    config = Config(
        weintek_allow=True,
        weintek_opcua=(WeintekOpcUaTarget("opc.tcp://hmi.local:4840", ("ns=2;s=T",), SECURE_OPCUA),),
    )

    target = policy.check_weintek_opcua(config, "opc.tcp://hmi.local:4840", "ns=2;s=T")

    assert target.security.mode == "SignAndEncrypt"
    assert target.security.certificate == "/etc/pki/client.der"


def test_mqtt_refuses_a_cleartext_target_that_never_opted_in():
    config = Config(
        weintek_allow=True,
        weintek_mqtt=(
            WeintekMqttTarget("hmi.local", 1883, ("cMT/temp",), MqttSecurity(tls=False)),
        ),
    )

    with pytest.raises(ToolError) as excinfo:
        policy.check_weintek_mqtt(config, "hmi.local", "cMT/temp", port=1883)
    assert excinfo.value.code == "INSECURE_TRANSPORT"
