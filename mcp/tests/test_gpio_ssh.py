"""SSH argument construction. No live SSH server is required."""

from types import SimpleNamespace

import pytest

from omarchy_hardware import gpio_ssh
from omarchy_hardware.config import Config
from omarchy_hardware.errors import ToolError


def _capture_run(monkeypatch, returncode=0, stdout="", stderr=""):
    captured: dict = {}

    def fake_run(args, **kwargs):
        captured["args"] = list(args)
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(gpio_ssh.subprocess, "run", fake_run)
    return captured


def test_run_uses_existing_host_key_checking_and_no_shell(monkeypatch):
    captured = _capture_run(monkeypatch, stdout="ok")
    config = Config(pi_hosts=("pi.local",), pi_ssh_timeout=7)

    result = gpio_ssh._run("pi.local", ["pinctrl", "get", "17"], config)

    assert result.returncode == 0
    args = captured["args"]
    assert args[0] == "ssh"
    assert captured["kwargs"]["shell"] is False
    assert "BatchMode=yes" in args
    assert "StrictHostKeyChecking=yes" in args
    assert "accept-new" not in args
    assert "ConnectTimeout=7" in args
    assert args[-3:] == ["--", "pi.local", "pinctrl get 17"]
    assert args.index("--") < args.index("pi.local")


def test_run_quotes_remote_argv_instead_of_interpolating(monkeypatch):
    captured = _capture_run(monkeypatch)
    gpio_ssh._run("pi.local", ["pinctrl", "set", "17", "op"], Config(pi_hosts=("pi.local",)))

    remote = captured["args"][-1]
    assert remote == "pinctrl set 17 op"
    assert ";" not in remote
    assert "$" not in remote
    assert captured["args"][-3:] == ["--", "pi.local", remote]


def test_unlisted_host_is_rejected_before_ssh(monkeypatch):
    captured = _capture_run(monkeypatch)
    with pytest.raises(ToolError) as excinfo:
        gpio_ssh._run("evil.example.com", ["pinctrl", "get"], Config(pi_hosts=("pi.local",)))
    assert excinfo.value.code == "HOST_NOT_ALLOWED"
    assert "args" not in captured


def test_unconfigured_hosts_are_rejected_before_ssh(monkeypatch):
    captured = _capture_run(monkeypatch)
    with pytest.raises(ToolError) as excinfo:
        gpio_ssh._run("pi.local", ["pinctrl", "get"], Config())
    assert excinfo.value.code == "HOST_NOT_ALLOWED"
    assert "args" not in captured


def test_read_pin_invokes_fixed_pinctrl_get(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(host, argv, config):
        calls.append(argv)
        if argv[:2] == ["command", "-v"]:
            return SimpleNamespace(returncode=0, stdout="/usr/bin/pinctrl\n", stderr="")
        return SimpleNamespace(
            returncode=0,
            stdout="17: op dh | hi // GPIO17 = output\n",
            stderr="",
        )

    monkeypatch.setattr(gpio_ssh, "_run", fake_run)
    gpio_ssh._BACKENDS.clear()

    pin = gpio_ssh.read_pin("pi.local", 17, Config(pi_hosts=("pi.local",)))

    assert pin["bcm"] == 17
    assert pin["level"] == 1
    assert ["command", "-v", "pinctrl"] in calls
    assert ["pinctrl", "get", "17"] in calls


def test_inventory_uses_bounded_fixed_reads(monkeypatch):
    calls: list[list[str]] = []
    outputs = {
        "/proc/device-tree/model": "Raspberry Pi 5 Model B Rev 1.0\x00",
        "/etc/os-release": 'ID=raspios\nPRETTY_NAME="Raspberry Pi OS"\nVERSION_ID="12"\n',
        "/proc/sys/kernel/osrelease": "6.6.31+rpt-rpi-2712\n",
        "/sys/class/thermal/thermal_zone0/temp": "42123\n",
        "/proc/loadavg": "0.12 0.08 0.04 1/100 42\n",
    }

    def fake_run(host, argv, config):
        calls.append(argv)
        if argv[:2] == ["command", "-v"]:
            return SimpleNamespace(returncode=0, stdout="/usr/bin/pinctrl\n", stderr="")
        path = argv[1]
        return SimpleNamespace(returncode=0, stdout=outputs[path], stderr="")

    monkeypatch.setattr(gpio_ssh, "_run", fake_run)
    gpio_ssh._BACKENDS.clear()
    result = gpio_ssh.inventory("pi.local", Config(pi_hosts=("pi.local",)))

    assert result["device"]["family"] == "raspberry_pi"
    assert result["device"]["capabilities"] == [
        "pi.status", "gpio.read", "gpio.list", "gpio.set_mode", "gpio.write"
    ]
    assert result["os"]["id"] == "raspios"
    assert result["kernel"] == "6.6.31+rpt-rpi-2712"
    assert result["temperature_c"] == 42.1
    assert result["load_average_1m"] == 0.12
    assert calls == [
        ["cat", "/proc/device-tree/model"],
        ["cat", "/etc/os-release"],
        ["cat", "/proc/sys/kernel/osrelease"],
        ["cat", "/sys/class/thermal/thermal_zone0/temp"],
        ["cat", "/proc/loadavg"],
        ["command", "-v", "pinctrl"],
    ]
