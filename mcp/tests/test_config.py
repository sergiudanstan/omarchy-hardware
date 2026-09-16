import os

import pytest

from omarchy_hardware import config


def _write_config(monkeypatch, tmp_path, text):
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    os.chmod(path, 0o600)
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    return path


def test_missing_config_uses_safe_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "missing.toml")

    loaded = config.load()

    assert loaded.pi_allowed_pins == tuple(range(2, 28))
    assert loaded.allow_flash is False
    assert loaded.sketch_roots == ()
    assert loaded.allow_unknown_serial is False
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
        "[jetson]\nhosts = [\"-oProxyCommand=evil\"]\n",
        "[pi]\nhosts = [\"shared.local\"]\n[jetson]\nhosts = [\"shared.local\"]\n",
        "[pi]\nssh_timeout = 0\n",
        "[pi]\nssh_timeout = 61\n",
        "[serial]\nmax_write_bytes = 0\n",
        "[serial]\nmax_write_bytes = 4097\n",
        "[serial]\nwrite_budget_bytes_per_min = 0\n",
        "[serial]\nwrite_budget_bytes_per_min = 65537\n",
        "[flash]\nallow = \"true\"\n",
        "[flash]\nallow = true\n",
        "[serial]\nallow_unknown = \"true\"\n",
        "[flash]\nallow = true\nsketch_roots = [\"~/Arduino\", \"~/Arduino\"]\n",
        "[flash]\nsketch_roots = [\"~/Arduino\\n/etc\"]\n",
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
allow = true
sketch_roots = ["~/Arduino", "/home/dan/Work"]
""",
    )

    loaded = config.load()

    assert loaded.pi_hosts == ("raspberrypi.local", "my-pi.local", "2001:db8::1")
    assert loaded.pi_allowed_pins == (2, 17, 27)
    assert loaded.pi_ssh_timeout == 30
    assert loaded.max_write_bytes == 2048
    assert loaded.write_budget_bytes_per_min == 32768
    assert loaded.allow_flash is True
    assert loaded.sketch_roots == ("~/Arduino", "/home/dan/Work")
    assert loaded.allow_unknown_serial is False
    assert loaded.weintek_allow is False
    assert loaded.weintek_opcua == ()
    assert loaded.weintek_mqtt == ()
    assert loaded.jetson_hosts == ()


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


@pytest.mark.parametrize("extra_bits", [0o040, 0o020, 0o010, 0o004, 0o002, 0o001, 0o044, 0o066], ids=oct)
def test_group_or_world_accessible_config_is_rejected(monkeypatch, tmp_path, extra_bits):
    # The file on disk stays 0o600. fstat of that one file reports the extra
    # permission bits, so load() runs its real open/fstat path without the test
    # ever creating a group- or world-accessible file.
    path = _write_config(monkeypatch, tmp_path, "[pi]\nhosts = []\n")
    inode = path.stat().st_ino
    real_fstat = os.fstat

    def fstat_with_extra_bits(fd):
        result = real_fstat(fd)
        if result.st_ino != inode:
            return result
        fields = list(result)
        fields[0] = result.st_mode | extra_bits
        return os.stat_result(fields)

    monkeypatch.setattr(config.os, "fstat", fstat_with_extra_bits)

    with pytest.raises(config.ConfigError, match="group- or world-accessible"):
        config.load()


def test_symlink_config_is_rejected(monkeypatch, tmp_path):
    real = tmp_path / "real.toml"
    real.write_text("[pi]\nhosts = []\n", encoding="utf-8")
    os.chmod(real, 0o600)
    link = tmp_path / "config.toml"
    link.symlink_to(real)
    monkeypatch.setattr(config, "CONFIG_PATH", link)

    with pytest.raises(config.ConfigError, match="regular file"):
        config.load()


def test_allow_unknown_serial_is_loaded(monkeypatch, tmp_path):
    _write_config(monkeypatch, tmp_path, "[serial]\nallow_unknown = true\n")

    assert config.load().allow_unknown_serial is True
