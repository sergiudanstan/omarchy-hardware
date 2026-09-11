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
