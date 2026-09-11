"""User configuration, read from ~/.config/omarchy-hardware/config.toml."""

from __future__ import annotations

import os
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "omarchy-hardware"
CONFIG_PATH = CONFIG_DIR / "config.toml"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "omarchy-hardware"

# BCM 0 and 1 are the HAT ID EEPROM pins; driving them can confuse board detection.
DEFAULT_PINS = tuple(n for n in range(2, 28))
MIN_SSH_TIMEOUT = 1
MAX_SSH_TIMEOUT = 60
MIN_WRITE_BYTES = 1
MAX_WRITE_BYTES = 4096
MIN_WRITE_BUDGET = 1
MAX_WRITE_BUDGET = 65536
MIN_MQTT_PORT = 1
MAX_MQTT_PORT = 65535
DEFAULT_MQTT_PORT = 1883
DEFAULT_OPCUA_PORT = 4840
MAX_TOPIC_LENGTH = 128
MAX_NODE_LENGTH = 256


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class WeintekOpcUaTarget:
    endpoint: str
    nodes: tuple[str, ...]


@dataclass(frozen=True)
class WeintekMqttTarget:
    host: str
    port: int
    topics: tuple[str, ...]


@dataclass(frozen=True)
class Config:
    pi_hosts: tuple[str, ...] = ()
    pi_allowed_pins: tuple[int, ...] = DEFAULT_PINS
    pi_ssh_timeout: int = 10
    max_write_bytes: int = 4096
    write_budget_bytes_per_min: int = 65536
    allow_flash: bool = True
    weintek_allow: bool = False
    weintek_opcua: tuple[WeintekOpcUaTarget, ...] = ()
    weintek_mqtt: tuple[WeintekMqttTarget, ...] = ()


def _valid_host(host: object) -> bool:
    """Accept SSH destinations we can pass as a single argv word.

    Reject leading '-' so a configured host cannot be parsed as an ssh option,
    and reject '/' so a path cannot be smuggled in as a destination. Hyphens
    inside a hostname (my-pi.local) and IPv6 addresses remain valid.
    """
    if not isinstance(host, str) or not host:
        return False
    if host.startswith("-") or "/" in host:
        return False
    return not any(char.isspace() or not char.isprintable() for char in host)


def _unique_tokens(values: object, *, label: str, max_length: int, extra_chars: str = "") -> tuple[str, ...]:
    if not isinstance(values, list) or not values:
        raise ConfigError(f"{label} must be a non-empty list of strings")
    tokens: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value or any(char.isspace() or not char.isprintable() for char in value):
            raise ConfigError(f"{label} must contain printable strings without whitespace")
        if len(value) > max_length or any(char in value for char in extra_chars):
            raise ConfigError(f"{label} entries must be at most {max_length} characters and exact matches")
        tokens.append(value)
    if len(set(tokens)) != len(tokens):
        raise ConfigError(f"{label} must not contain duplicates")
    return tuple(tokens)


def _opcua_endpoint(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError("weintek.opcua.endpoint must be an opc.tcp:// URL")
    parsed = urlparse(value)
    if parsed.scheme != "opc.tcp" or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigError("weintek.opcua.endpoint must be opc.tcp://host:port with no credentials or query")
    if parsed.path not in ("", "/"):
        raise ConfigError("weintek.opcua.endpoint must not include a path")
    host = parsed.hostname
    if host is None or not _valid_host(host):
        raise ConfigError("weintek.opcua.endpoint host is not an allowed hostname or address")
    port = parsed.port or DEFAULT_OPCUA_PORT
    if ":" in host:
        return f"opc.tcp://[{host}]:{port}"
    return f"opc.tcp://{host}:{port}"


def _parse_weintek(raw: dict) -> tuple[bool, tuple[WeintekOpcUaTarget, ...], tuple[WeintekMqttTarget, ...]]:
    weintek = raw.get("weintek", {})
    if not weintek:
        return False, (), ()
    if not isinstance(weintek, dict):
        raise ConfigError("weintek must be a table")
    allow = weintek.get("allow", False)
    if not isinstance(allow, bool):
        raise ConfigError("weintek.allow must be a boolean")

    opcua_raw = weintek.get("opcua", [])
    if not isinstance(opcua_raw, list):
        raise ConfigError("weintek.opcua must be an array of tables")
    opcua: list[WeintekOpcUaTarget] = []
    endpoints: list[str] = []
    for entry in opcua_raw:
        if not isinstance(entry, dict):
            raise ConfigError("weintek.opcua entries must be tables")
        endpoint = _opcua_endpoint(entry.get("endpoint"))
        nodes = _unique_tokens(entry.get("nodes"), label="weintek.opcua.nodes", max_length=MAX_NODE_LENGTH)
        endpoints.append(endpoint)
        opcua.append(WeintekOpcUaTarget(endpoint=endpoint, nodes=nodes))
    if len(set(endpoints)) != len(endpoints):
        raise ConfigError("weintek.opcua endpoints must be unique")

    mqtt_raw = weintek.get("mqtt", [])
    if not isinstance(mqtt_raw, list):
        raise ConfigError("weintek.mqtt must be an array of tables")
    mqtt: list[WeintekMqttTarget] = []
    mqtt_keys: list[tuple[str, int]] = []
    for entry in mqtt_raw:
        if not isinstance(entry, dict):
            raise ConfigError("weintek.mqtt entries must be tables")
        host = entry.get("host")
        if not _valid_host(host):
            raise ConfigError("weintek.mqtt.host must be a hostname or address without paths or leading dashes")
        port = entry.get("port", DEFAULT_MQTT_PORT)
        if not isinstance(port, int) or isinstance(port, bool) or not MIN_MQTT_PORT <= port <= MAX_MQTT_PORT:
            raise ConfigError(f"weintek.mqtt.port must be an integer in {MIN_MQTT_PORT}-{MAX_MQTT_PORT}")
        topics = _unique_tokens(
            entry.get("topics"),
            label="weintek.mqtt.topics",
            max_length=MAX_TOPIC_LENGTH,
            extra_chars="+#",
        )
        mqtt_keys.append((host, port))
        mqtt.append(WeintekMqttTarget(host=host, port=port, topics=topics))
    if len(set(mqtt_keys)) != len(mqtt_keys):
        raise ConfigError("weintek.mqtt host and port pairs must be unique")
    return allow, tuple(opcua), tuple(mqtt)


def _check_permissions(path: Path) -> None:
    mode = path.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ConfigError(
            f"{path} is group- or world-accessible; it names the hosts this plugin may "
            f"reach over SSH. Run: chmod 600 {path}"
        )


def load() -> Config:
    if not CONFIG_PATH.exists():
        return Config()

    _check_permissions(CONFIG_PATH)

    with CONFIG_PATH.open("rb") as handle:
        raw = tomllib.load(handle)

    pi = raw.get("pi", {})
    serial = raw.get("serial", {})
    flash = raw.get("flash", {})

    pins = pi.get("allowed_pins", list(DEFAULT_PINS))
    if (
        not isinstance(pins, list)
        or not all(isinstance(pin, int) and not isinstance(pin, bool) and 2 <= pin <= 27 for pin in pins)
        or len(set(pins)) != len(pins)
    ):
        raise ConfigError("pi.allowed_pins must contain unique integers in 2-27")

    hosts = pi.get("hosts", [])
    if not isinstance(hosts, list) or not all(_valid_host(host) for host in hosts) or len(set(hosts)) != len(hosts):
        raise ConfigError(
            "pi.hosts must contain unique hostnames or addresses without whitespace, "
            "paths, leading dashes, or control characters"
        )

    ssh_timeout = pi.get("ssh_timeout", 10)
    max_write_bytes = serial.get("max_write_bytes", 4096)
    write_budget_bytes_per_min = serial.get("write_budget_bytes_per_min", 65536)
    allow_flash = flash.get("allow", True)
    if (
        not isinstance(ssh_timeout, int)
        or isinstance(ssh_timeout, bool)
        or not MIN_SSH_TIMEOUT <= ssh_timeout <= MAX_SSH_TIMEOUT
    ):
        raise ConfigError(f"pi.ssh_timeout must be an integer in {MIN_SSH_TIMEOUT}-{MAX_SSH_TIMEOUT}")
    if (
        not isinstance(max_write_bytes, int)
        or isinstance(max_write_bytes, bool)
        or not MIN_WRITE_BYTES <= max_write_bytes <= MAX_WRITE_BYTES
    ):
        raise ConfigError(f"serial.max_write_bytes must be an integer in {MIN_WRITE_BYTES}-{MAX_WRITE_BYTES}")
    if (
        not isinstance(write_budget_bytes_per_min, int)
        or isinstance(write_budget_bytes_per_min, bool)
        or not MIN_WRITE_BUDGET <= write_budget_bytes_per_min <= MAX_WRITE_BUDGET
    ):
        raise ConfigError(
            f"serial.write_budget_bytes_per_min must be an integer in {MIN_WRITE_BUDGET}-{MAX_WRITE_BUDGET}"
        )
    if not isinstance(allow_flash, bool):
        raise ConfigError("flash.allow must be a boolean")

    weintek_allow, weintek_opcua, weintek_mqtt = _parse_weintek(raw)

    return Config(
        pi_hosts=tuple(hosts),
        pi_allowed_pins=tuple(pins),
        pi_ssh_timeout=ssh_timeout,
        max_write_bytes=max_write_bytes,
        write_budget_bytes_per_min=write_budget_bytes_per_min,
        allow_flash=allow_flash,
        weintek_allow=weintek_allow,
        weintek_opcua=weintek_opcua,
        weintek_mqtt=weintek_mqtt,
    )
