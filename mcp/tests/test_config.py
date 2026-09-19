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
        # TLS off without saying so explicitly.
        """[weintek]
allow = true
[[weintek.mqtt]]
host = "h"
topics = ["t"]
security = { tls = false }
""",
        # A security mode with no client certificate to present.
        """[weintek]
allow = true
[[weintek.opcua]]
endpoint = "opc.tcp://h:4840"
nodes = ["n"]
security = { mode = "Sign" }
""",
        # A policy nobody should still be offering.
        """[weintek]
allow = true
[[weintek.opcua]]
endpoint = "opc.tcp://h:4840"
nodes = ["n"]
security = { policy = "Basic128Rsa15", mode = "Sign" }
""",
        # Half a credential.
        """[weintek]
allow = true
[[weintek.mqtt]]
host = "h"
topics = ["t"]
security = { username = "operator" }
""",
        "[pi]\nactuation_budget_per_min = 0\n",
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
security = { certificate = "/etc/pki/hmi.der", private_key = "/etc/pki/hmi.key" }

[[weintek.mqtt]]
host = "192.168.1.50"
port = 1883
topics = ["cMT/machine/temp"]
security = { tls = false, allow_insecure = true }
""",
    )

    loaded = config.load()

    assert loaded.weintek_allow is True
    assert loaded.weintek_opcua[0].endpoint == "opc.tcp://192.168.1.50:4840"
    assert loaded.weintek_opcua[0].nodes == ("ns=2;s=Temperature", "ns=2;s=Pressure")
    assert loaded.weintek_mqtt[0].host == "192.168.1.50"
    assert loaded.weintek_mqtt[0].topics == ("cMT/machine/temp",)

    # Defaults are the secure ones, and the insecure MQTT target had to say so.
    assert loaded.weintek_opcua[0].security.mode == "SignAndEncrypt"
    assert loaded.weintek_opcua[0].security.policy == "Basic256Sha256"
    assert loaded.weintek_mqtt[0].security.allow_insecure is True


def test_weintek_defaults_to_tls_and_its_port(monkeypatch, tmp_path):
    """A target that names no port must not silently keep talking to 1883."""
    _write_config(
        monkeypatch,
        tmp_path,
        """[weintek]
allow = true

[[weintek.mqtt]]
host = "192.168.1.50"
topics = ["cMT/machine/temp"]
""",
    )

    target = config.load().weintek_mqtt[0]

    assert target.security.tls is True
    assert target.port == config.DEFAULT_MQTT_TLS_PORT


def test_weintek_opcua_accepts_no_security_only_when_said_out_loud(monkeypatch, tmp_path):
    insecure = """[weintek]
allow = true

[[weintek.opcua]]
endpoint = "opc.tcp://192.168.1.50:4840"
nodes = ["ns=2;s=Temperature"]
security = { policy = "None", mode = "None"%s }
"""
    _write_config(monkeypatch, tmp_path, insecure % "")
    with pytest.raises(config.ConfigError, match="allow_insecure"):
        config.load()

    _write_config(monkeypatch, tmp_path, insecure % ", allow_insecure = true")
    assert config.load().weintek_opcua[0].security.mode == "None"


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


MING_GOOD = """
[ming]
allow = true
timeout = 5
[[ming.mqtt]]
name = "local"
host = "127.0.0.1"
subscribe = ["sensors/#", "plant/+/temp"]
publish = ["actuators/fan"]
security = { tls = false }
[[ming.mqtt]]
name = "remote"
host = "broker.lan"
security = { username = "claude", password_file = "~/.config/omarchy-hardware/mqtt-password" }
[[ming.influxdb]]
name = "local"
url = "http://localhost:8086/"
org = "home"
read_buckets = ["sensors"]
write_buckets = ["claude"]
security = { token_file = "~/.config/omarchy-hardware/influx-token" }
[[ming.nodered]]
name = "local"
url = "http://[::1]:1880"
inject_nodes = ["a1b2c3.d4"]
[[ming.grafana]]
name = "remote"
url = "https://grafana.lan/grafana"
annotate = true
"""


def test_ming_section_parses(monkeypatch, tmp_path):
    _write_config(monkeypatch, tmp_path, MING_GOOD)
    loaded = config.load()

    assert loaded.ming_allow is True and loaded.ming_timeout == 5
    local, remote = loaded.ming_mqtt
    # Loopback may skip TLS without a waiver; the defaults follow the TLS choice.
    assert (local.port, local.security.tls) == (1883, False)
    assert (remote.port, remote.security.tls, remote.security.password_file) == (
        8883,
        True,
        "~/.config/omarchy-hardware/mqtt-password",
    )
    assert local.subscribe == ("sensors/#", "plant/+/temp")
    assert loaded.ming_influxdb[0].url == "http://localhost:8086"
    assert loaded.ming_nodered[0].url == "http://[::1]:1880"
    assert loaded.ming_grafana[0].url == "https://grafana.lan/grafana"


def test_ming_defaults_to_off(monkeypatch, tmp_path):
    _write_config(monkeypatch, tmp_path, "[ming]\n")
    loaded = config.load()
    assert loaded.ming_allow is False
    assert loaded.ming_mqtt == loaded.ming_influxdb == loaded.ming_nodered == loaded.ming_grafana == ()


@pytest.mark.parametrize(
    ("section", "message"),
    [
        ('[[ming.mqtt]]\nname="a"\nhost="broker.lan"\nsecurity={tls=false}\n', "cleartext"),
        ('[[ming.grafana]]\nname="a"\nurl="http://grafana.lan"\n', "cleartext"),
        ('[[ming.grafana]]\nname="A B"\nurl="https://g.lan"\n', "name must be"),
        (
            '[[ming.grafana]]\nname="a"\nurl="https://g.lan"\n[[ming.grafana]]\nname="a"\nurl="https://h.lan"\n',
            "unique",
        ),
        ('[[ming.grafana]]\nname="a"\nurl="https://u:p@g.lan"\n', "credentials"),
        ('[[ming.grafana]]\nname="a"\nurl="https://g.lan/?x=1"\n', "credentials"),
        ('[[ming.grafana]]\nname="a"\nurl="https://g.lan/a/../b"\n', "plain prefix"),
        ('[[ming.grafana]]\nname="a"\nurl="file:///etc/passwd"\n', "http:// or https://"),
        ('[[ming.grafana]]\nname="a"\nurl="https://g.lan:99999"\n', "invalid port"),
        ('[[ming.mqtt]]\nname="a"\nhost="localhost"\nsubscribe=["a/#/b"]\n', "where MQTT does not allow"),
        ('[[ming.mqtt]]\nname="a"\nhost="localhost"\nsubscribe=["a/b+"]\n', "where MQTT does not allow"),
        ('[[ming.mqtt]]\nname="a"\nhost="localhost"\npublish=["a/#"]\n', "exact matches"),
        ('[[ming.mqtt]]\nname="a"\nhost="localhost"\npublish=["$SYS/x"]\n', "broker-reserved"),
        ('[[ming.mqtt]]\nname="a"\nhost="-oProxy"\n', "hostname or address"),
        ('[[ming.mqtt]]\nname="a"\nhost="b.lan"\nsecurity={username="u"}\n', "set together"),
        ('[[ming.mqtt]]\nname="a"\nhost="b.lan"\nsecurity={username="u", password_env="P", password_file="/x"}\n',
         "alternatives"),
        ('[[ming.grafana]]\nname="a"\nurl="https://g.lan"\nsecurity={token_env="T", token_file="/x"}\n',
         "alternatives"),
        ('[[ming.nodered]]\nname="a"\nurl="https://n.lan"\ninject_nodes=["a;b"]\n', "node ids"),
        ('[[ming.influxdb]]\nname="a"\nurl="https://i.lan"\n', "org must be"),
        ("[ming]\ntimeout = 0\n", "timeout"),
        ("[ming]\nmax_payload_bytes = 70000\n", "max_payload_bytes"),
        ("[ming]\nallow = \"yes\"\n", "boolean"),
        ('ming = "x"\n', "must be a table"),
    ],
)
def test_ming_rejects_unsafe_or_malformed_targets(monkeypatch, tmp_path, section, message):
    _write_config(monkeypatch, tmp_path, section)
    with pytest.raises(config.ConfigError, match=message):
        config.load()


def test_allow_fingerprinted_must_be_boolean(monkeypatch, tmp_path):
    _write_config(monkeypatch, tmp_path, '[flash]\nallow_fingerprinted = "yes"\n')
    with pytest.raises(config.ConfigError, match="allow_fingerprinted"):
        config.load()
    _write_config(monkeypatch, tmp_path, "[flash]\nallow_fingerprinted = true\n")
    assert config.load().allow_fingerprinted is True
