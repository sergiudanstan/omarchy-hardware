from types import SimpleNamespace

import pytest

from omarchy_hardware import gpio_ssh, jetson_ssh, server
from omarchy_hardware.config import Config
from omarchy_hardware.errors import ToolError
from omarchy_hardware.jetson_ssh import parse_l4t_release


def test_parse_l4t_release():
    assert parse_l4t_release("# R36 (release), REVISION: 3.0, GCID: 1") == "36.3.0"


def test_jetson_inventory_uses_fixed_reads_and_not_gpio(monkeypatch):
    calls: list[list[str]] = []
    files = {
        "/proc/device-tree/model": "NVIDIA Jetson Orin Nano Developer Kit\x00",
        "/etc/os-release": 'ID=ubuntu\nPRETTY_NAME="Ubuntu 22.04"\nVERSION_ID="22.04"\n',
        "/proc/sys/kernel/osrelease": "5.15.0-tegra\n",
        "/etc/nv_tegra_release": "# R36 (release), REVISION: 3.0\n",
        "/sys/devices/virtual/thermal/thermal_zone0/temp": "42123\n",
        "/proc/loadavg": "0.50 0.40 0.30 1/100 42\n",
        "/sys/class/net/eth0/operstate": "up\n",
    }

    def fake_run(host, argv, config, **kwargs):
        assert kwargs.get("hosts") == ("orin.local",)
        calls.append(argv)
        if argv[:2] == ["command", "-v"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        if argv == ["df", "-P", "/"]:
            return SimpleNamespace(
                returncode=0,
                stdout="Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/mmcblk0p1 1 1 1 1% /\n",
                stderr="",
            )
        if argv[:1] == ["cat"] and argv[1] in files:
            return SimpleNamespace(returncode=0, stdout=files[argv[1]], stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(jetson_ssh, "_run", fake_run)
    result = jetson_ssh.inventory("orin.local", Config(jetson_hosts=("orin.local",)))

    assert result["generation"] == "orin"
    assert result["l4t"] == "36.3.0"
    assert result["device"]["family"] == "jetson"
    assert "gpio.write" not in result["device"]["capabilities"]
    assert all(argv[0] != "pinctrl" and argv[0] != "raspi-gpio" for argv in calls)


def test_gpio_refuses_jetson_allowlisted_host():
    with pytest.raises(ToolError) as excinfo:
        gpio_ssh.detect_backend("orin.local", Config(pi_hosts=("orin.local",), jetson_hosts=("orin.local",)))
    assert excinfo.value.code == "HOST_NOT_ALLOWED"


def test_jetson_status_rejects_unlisted_host():
    with pytest.raises(ToolError) as excinfo:
        jetson_ssh.status("evil.example.com", Config(jetson_hosts=("orin.local",)))
    assert excinfo.value.code == "HOST_NOT_ALLOWED"


def test_jetson_errors_do_not_list_hosts(monkeypatch):
    monkeypatch.setattr(server, "_config", lambda: Config(jetson_hosts=("secret-orin.local", "other-orin.local")))
    result = server.jetson_status()
    assert result["ok"] is False
    assert "secret-orin.local" not in str(result)
