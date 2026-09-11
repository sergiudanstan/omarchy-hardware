import os

import pytest

from omarchy_hardware import policy
from omarchy_hardware.config import Config, WeintekMqttTarget, WeintekOpcUaTarget
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
    config = Config(pi_hosts=("pi.local",))
    assert policy.check_host("pi.local", config) == "pi.local"

    with pytest.raises(ToolError):
        policy.check_host("evil.example.com", config)


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


def test_weintek_opcua_requires_allow_and_exact_node():
    config = Config(
        weintek_allow=True,
        weintek_opcua=(WeintekOpcUaTarget("opc.tcp://hmi.local:4840", ("ns=2;s=T",)),),
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
    policy.check_weintek_mqtt(config, "hmi.local", "cMT/temp")
    with pytest.raises(ToolError) as excinfo:
        policy.check_weintek_mqtt(config, "hmi.local", "cMT/other")
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
