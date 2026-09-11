import os

import pytest

from omarchy_hardware import config


def _write_config(monkeypatch, tmp_path, text, mode=0o600):
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    os.chmod(path, mode)
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    return path


def test_missing_config_uses_safe_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "missing.toml")

    loaded = config.load()

    assert loaded.pi_allowed_pins == tuple(range(2, 28))
    assert loaded.allow_flash is True
    assert not hasattr(loaded, "pi_host_keys")


@pytest.mark.parametrize(
    "section",
    [
        "[pi]\nallowed_pins = [0]\n",
        "[pi]\nallowed_pins = [2, 2]\n",
        "[pi]\nhosts = [\"pi.local\", \"pi.local\"]\n",
        "[pi]\nhosts = [\"pi local\"]\n",
        "[pi]\nhosts = [\"pi.local\\u0001\"]\n",
        "[pi]\nhosts = [\"-oProxyCommand=evil\"]\n",
        "[pi]\nhosts = [\"/tmp/socket\"]\n",
        "[pi]\nhosts = [\"/etc/passwd\"]\n",
        "[pi]\nssh_timeout = 0\n",
        "[pi]\nssh_timeout = 61\n",
        "[serial]\nmax_write_bytes = 0\n",
        "[serial]\nmax_write_bytes = 4097\n",
        "[serial]\nwrite_budget_bytes_per_min = 0\n",
        "[serial]\nwrite_budget_bytes_per_min = 65537\n",
        "[flash]\nallow = \"true\"\n",
        "[weintek]\nallow = \"true\"\n",
        "[[weintek.opcua]]\nendpoint = \"http://hmi.local\"\nnodes = [\"ns=2;s=T\"]\n",
        "[[weintek.opcua]]\nendpoint = \"opc.tcp://user:pass@hmi.local:4840\"\nnodes = [\"ns=2;s=T\"]\n",
        "[[weintek.opcua]]\nendpoint = \"opc.tcp://hmi.local:4840\"\nnodes = []\n",
        "[[weintek.mqtt]]\nhost = \"hmi.local\"\ntopics = [\"cMT/+/temp\"]\n",
        "[[weintek.mqtt]]\nhost = \"hmi.local\"\nport = 0\ntopics = [\"cMT/temp\"]\n",
        "[[weintek.mqtt]]\nhost = \"-bad\"\ntopics = [\"cMT/temp\"]\n",
    ],
)
def test_invalid_config_is_rejected(monkeypatch, tmp_path, section):
    _write_config(monkeypatch, tmp_path, section)

    with pytest.raises(config.ConfigError):
        config.load()


def test_valid_config_is_loaded(monkeypatch, tmp_path):
    _write_config(
        monkeypatch,
        tmp_path,
        """[pi]
hosts = ["raspberrypi.local", "my-pi.local", "2001:db8::1"]
allowed_pins = [2, 17, 27]
ssh_timeout = 30

[serial]
max_write_bytes = 2048
write_budget_bytes_per_min = 32768

[flash]
allow = false
""",
    )

    loaded = config.load()

    assert loaded.pi_hosts == ("raspberrypi.local", "my-pi.local", "2001:db8::1")
    assert loaded.pi_allowed_pins == (2, 17, 27)
    assert loaded.pi_ssh_timeout == 30
    assert loaded.max_write_bytes == 2048
    assert loaded.write_budget_bytes_per_min == 32768
    assert loaded.allow_flash is False
    assert loaded.weintek_allow is False
    assert loaded.weintek_opcua == ()
    assert loaded.weintek_mqtt == ()


def test_weintek_allowlists_are_loaded(monkeypatch, tmp_path):
    _write_config(
        monkeypatch,
        tmp_path,
        """[weintek]
allow = true

[[weintek.opcua]]
endpoint = "opc.tcp://192.168.1.50:4840"
nodes = ["ns=2;s=Temperature", "ns=2;s=Pressure"]

[[weintek.mqtt]]
host = "192.168.1.50"
port = 1883
topics = ["cMT/machine/temp"]
""",
    )

    loaded = config.load()

    assert loaded.weintek_allow is True
    assert loaded.weintek_opcua[0].endpoint == "opc.tcp://192.168.1.50:4840"
    assert loaded.weintek_opcua[0].nodes == ("ns=2;s=Temperature", "ns=2;s=Pressure")
    assert loaded.weintek_mqtt[0].host == "192.168.1.50"
    assert loaded.weintek_mqtt[0].topics == ("cMT/machine/temp",)


def test_group_or_world_accessible_config_is_rejected(monkeypatch, tmp_path):
    _write_config(monkeypatch, tmp_path, "[pi]\nhosts = []\n", mode=0o644)

    with pytest.raises(config.ConfigError, match="group- or world-accessible"):
        config.load()
