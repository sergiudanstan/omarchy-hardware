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
    assert "ForwardAgent=no" in args
    assert "ForwardX11=no" in args
    assert "PermitLocalCommand=no" in args
    assert "ClearAllForwardings=yes" in args
    assert "ProxyCommand=none" in args
    assert "ForwardAgent=yes" not in args
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
    gpio_ssh._TOOLS.clear()

    pin = gpio_ssh.read_pin("pi.local", 17, Config(pi_hosts=("pi.local",)))

    assert pin["bcm"] == 17
    assert pin["level"] == 1
    assert ["command", "-v", "pinctrl"] in calls
    assert ["pinctrl", "get", "17"] in calls


def test_read_pin_rejects_stdout_for_a_different_bcm(monkeypatch):
    def fake_run(host, argv, config):
        if argv[:2] == ["command", "-v"]:
            return SimpleNamespace(returncode=0, stdout="/usr/bin/pinctrl\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="4: op dh | hi // GPIO4 = output\n", stderr="")

    monkeypatch.setattr(gpio_ssh, "_run", fake_run)
    gpio_ssh._BACKENDS.clear()
    gpio_ssh._TOOLS.clear()

    with pytest.raises(ToolError) as excinfo:
        gpio_ssh.read_pin("pi.local", 17, Config(pi_hosts=("pi.local",)))
    assert excinfo.value.code == "SSH_FAILED"


def test_inventory_uses_bounded_fixed_reads(monkeypatch):
    calls: list[list[str]] = []
    outputs = {
        "/proc/device-tree/model": "Raspberry Pi 5 Model B Rev 1.0\x00",
        "/etc/os-release": 'ID=raspios\nPRETTY_NAME="Raspberry Pi OS"\nVERSION_ID="12"\n',
        "/proc/sys/kernel/osrelease": "6.6.31+rpt-rpi-2712\n",
        "/sys/class/thermal/thermal_zone0/temp": "42123\n",
        "/proc/loadavg": "0.12 0.08 0.04 1/100 42\n",
        "/sys/class/net/eth0/operstate": "up\n",
        "/sys/class/net/wlan0/operstate": "down\n",
        "/sys/class/net/end0/operstate": "up\n",
    }
    tools = {"pinctrl": "/usr/bin/pinctrl\n", "raspi-gpio": "", "vcgencmd": "/usr/bin/vcgencmd\n"}

    def fake_run(host, argv, config):
        calls.append(argv)
        if argv[:2] == ["command", "-v"]:
            stdout = tools.get(argv[2], "")
            return SimpleNamespace(returncode=0 if stdout else 1, stdout=stdout, stderr="")
        if argv == ["vcgencmd", "get_throttled"]:
            return SimpleNamespace(returncode=0, stdout="throttled=0x50000\n", stderr="")
        if argv == ["df", "-P", "/"]:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
                    "/dev/mmcblk0p2 30449664 12000000 16000000 43% /\n"
                ),
                stderr="",
            )
        path = argv[1]
        stdout = outputs.get(path)
        if stdout is None:
            return SimpleNamespace(returncode=1, stdout="", stderr="missing")
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(gpio_ssh, "_run", fake_run)
    gpio_ssh._BACKENDS.clear()
    gpio_ssh._TOOLS.clear()
    result = gpio_ssh.inventory("pi.local", Config(pi_hosts=("pi.local",)))

    assert result["device"]["family"] == "raspberry_pi"
    assert result["generation"] == "pi5"
    assert result["gpio_backend"] == "pinctrl"
    assert result["tools"] == {"pinctrl": True, "raspi-gpio": False, "vcgencmd": True}
    assert result["throttled"]["under_voltage_occurred"] is True
    assert result["throttled"]["currently_throttled"] is False
    assert result["storage"]["capacity_percent"] == 43
    assert result["storage"]["mounted_on"] == "/"
    assert result["network"] == [
        {"name": "eth0", "operstate": "up"},
        {"name": "wlan0", "operstate": "down"},
        {"name": "end0", "operstate": "up"},
    ]
    assert result["warnings"] == []
    assert result["temperature_c"] == 42.1
    assert ["vcgencmd", "get_throttled"] in calls
    assert ["df", "-P", "/"] in calls
    assert ["command", "-v", "vcgencmd"] in calls


def test_pi5_raspi_gpio_emits_warning():
    assert gpio_ssh.pi_generation("Raspberry Pi 4 Model B") == "pi4"
    assert gpio_ssh.parse_throttled("throttled=0x0")["currently_throttled"] is False
    assert gpio_ssh.parse_df_root("Filesystem Used\n/dev/root 1 2 3 9% /\n")["available_kib"] == 3


def test_inventory_warns_when_pi5_uses_raspi_gpio(monkeypatch):
    def fake_run(host, argv, config):
        if argv[:2] == ["command", "-v"]:
            present = argv[2] == "raspi-gpio"
            return SimpleNamespace(
                returncode=0 if present else 1,
                stdout="/usr/bin/raspi-gpio\n" if present else "",
                stderr="",
            )
        if argv[0] == "cat" and argv[1] == "/proc/device-tree/model":
            return SimpleNamespace(returncode=0, stdout="Raspberry Pi 5 Model B\x00", stderr="")
        if argv[0] == "cat":
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        if argv == ["df", "-P", "/"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(gpio_ssh, "_run", fake_run)
    gpio_ssh._BACKENDS.clear()
    gpio_ssh._TOOLS.clear()
    result = gpio_ssh.inventory("pi.local", Config(pi_hosts=("pi.local",)))
    assert result["generation"] == "pi5"
    assert result["gpio_backend"] == "raspi-gpio"
    assert result["warnings"]
