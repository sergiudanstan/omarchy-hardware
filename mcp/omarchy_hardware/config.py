"""User configuration, read from ~/.config/omarchy-hardware/config.toml."""

from __future__ import annotations

import errno
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
MIN_WRITE_TIMEOUT_MS = 100
MAX_WRITE_TIMEOUT_MS = 10_000
MIN_WRITE_BUDGET = 1
MAX_WRITE_BUDGET = 65536
MIN_ACTUATION_BUDGET = 1
MAX_ACTUATION_BUDGET = 10_000
MIN_MQTT_PORT = 1
MAX_MQTT_PORT = 65535
DEFAULT_MQTT_PORT = 1883
DEFAULT_MQTT_TLS_PORT = 8883
DEFAULT_OPCUA_PORT = 4840
# OPC UA security policies worth offering: the deprecated Basic128Rsa15 and
# Basic256 are deliberately absent rather than available and discouraged.
OPCUA_POLICIES = frozenset({"None", "Basic256Sha256", "Aes128_Sha256_RsaOaep", "Aes256_Sha256_RsaPss"})
OPCUA_MODES = frozenset({"None", "Sign", "SignAndEncrypt"})
MAX_TOPIC_LENGTH = 128
MAX_NODE_LENGTH = 256


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class OpcUaSecurity:
    """How a session to an OPC UA endpoint must be secured.

    Defaults are the secure ones. Reaching an HMI with no signing and no
    encryption is possible, but only by writing `allow_insecure = true` next to
    it in the config file -- a plant network is not a reason to skip this, it is
    the reason for it.
    """

    policy: str = "Basic256Sha256"
    mode: str = "SignAndEncrypt"
    certificate: str | None = None
    private_key: str | None = None
    trust_list: str | None = None
    username: str | None = None
    password_env: str | None = None
    allow_insecure: bool = False


@dataclass(frozen=True)
class MqttSecurity:
    """Transport security for an MQTT connection. TLS unless explicitly waived."""

    tls: bool = True
    ca_file: str | None = None
    client_certificate: str | None = None
    client_key: str | None = None
    username: str | None = None
    password_env: str | None = None
    allow_insecure: bool = False


@dataclass(frozen=True)
class WeintekOpcUaTarget:
    endpoint: str
    nodes: tuple[str, ...]
    security: OpcUaSecurity = OpcUaSecurity()


@dataclass(frozen=True)
class WeintekMqttTarget:
    host: str
    port: int
    topics: tuple[str, ...]
    security: MqttSecurity = MqttSecurity()


@dataclass(frozen=True)
class Config:
    pi_hosts: tuple[str, ...] = ()
    jetson_hosts: tuple[str, ...] = ()
    pi_allowed_pins: tuple[int, ...] = DEFAULT_PINS
    pi_ssh_timeout: int = 10
    actuation_budget_per_min: int = 120
    max_write_bytes: int = 4096
    write_timeout_ms: int = 2_000
    write_budget_bytes_per_min: int = 65536
    allow_unknown_serial: bool = False
    allow_flash: bool = False
    sketch_roots: tuple[str, ...] = ()
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


def _optional_path(entry: dict, key: str, label: str) -> str | None:
    """A filesystem path naming a key, certificate or trust list."""
    value = entry.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value or "\x00" in value or any(c in value for c in "\n\r"):
        raise ConfigError(f"{label}.{key} must be a single-line path")
    return value


def _optional_name(entry: dict, key: str, label: str, max_length: int = 128) -> str | None:
    value = entry.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise ConfigError(f"{label}.{key} must be a non-empty string of at most {max_length} characters")
    if any(char.isspace() or not char.isprintable() for char in value):
        raise ConfigError(f"{label}.{key} must be printable and contain no whitespace")
    return value


def _flag(entry: dict, key: str, label: str, default: bool) -> bool:
    value = entry.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{label}.{key} must be a boolean")
    return value


def _opcua_security(entry: dict) -> OpcUaSecurity:
    raw = entry.get("security", {})
    if not isinstance(raw, dict):
        raise ConfigError("weintek.opcua.security must be a table")
    label = "weintek.opcua.security"

    policy = raw.get("policy", "Basic256Sha256")
    mode = raw.get("mode", "SignAndEncrypt")
    if policy not in OPCUA_POLICIES:
        raise ConfigError(f"{label}.policy must be one of: {', '.join(sorted(OPCUA_POLICIES))}")
    if mode not in OPCUA_MODES:
        raise ConfigError(f"{label}.mode must be one of: {', '.join(sorted(OPCUA_MODES))}")
    if (policy == "None") != (mode == "None"):
        raise ConfigError(f"{label}.policy and {label}.mode must both be 'None', or neither")

    allow_insecure = _flag(raw, "allow_insecure", label, False)
    certificate = _optional_path(raw, "certificate", label)
    private_key = _optional_path(raw, "private_key", label)

    if mode == "None":
        if not allow_insecure:
            raise ConfigError(
                f"{label}.mode is 'None', which sends OPC UA writes unsigned and unencrypted. "
                f"Set {label}.allow_insecure = true to accept that explicitly."
            )
    elif not (certificate and private_key):
        raise ConfigError(
            f"{label} requires certificate and private_key unless mode is 'None'; "
            "an OPC UA client certificate is how the HMI identifies this machine"
        )

    if (raw.get("username") is None) != (raw.get("password_env") is None):
        raise ConfigError(f"{label}.username and {label}.password_env must be set together")

    return OpcUaSecurity(
        policy=policy,
        mode=mode,
        certificate=certificate,
        private_key=private_key,
        trust_list=_optional_path(raw, "trust_list", label),
        username=_optional_name(raw, "username", label),
        password_env=_optional_name(raw, "password_env", label),
        allow_insecure=allow_insecure,
    )


def _mqtt_security(entry: dict) -> MqttSecurity:
    raw = entry.get("security", {})
    if not isinstance(raw, dict):
        raise ConfigError("weintek.mqtt.security must be a table")
    label = "weintek.mqtt.security"

    tls = _flag(raw, "tls", label, True)
    allow_insecure = _flag(raw, "allow_insecure", label, False)
    if not tls and not allow_insecure:
        raise ConfigError(
            f"{label}.tls is false, which publishes to the HMI in cleartext. "
            f"Set {label}.allow_insecure = true to accept that explicitly."
        )

    client_certificate = _optional_path(raw, "client_certificate", label)
    client_key = _optional_path(raw, "client_key", label)
    if bool(client_certificate) != bool(client_key):
        raise ConfigError(f"{label}.client_certificate and {label}.client_key must be set together")
    if (raw.get("username") is None) != (raw.get("password_env") is None):
        raise ConfigError(f"{label}.username and {label}.password_env must be set together")

    return MqttSecurity(
        tls=tls,
        ca_file=_optional_path(raw, "ca_file", label),
        client_certificate=client_certificate,
        client_key=client_key,
        username=_optional_name(raw, "username", label),
        password_env=_optional_name(raw, "password_env", label),
        allow_insecure=allow_insecure,
    )


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
        opcua.append(WeintekOpcUaTarget(endpoint=endpoint, nodes=nodes, security=_opcua_security(entry)))
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
        security = _mqtt_security(entry)
        # The default port follows the transport, so a config that turns TLS on
        # without naming a port does not silently keep talking to 1883.
        port = entry.get("port", DEFAULT_MQTT_TLS_PORT if security.tls else DEFAULT_MQTT_PORT)
        if not isinstance(port, int) or isinstance(port, bool) or not MIN_MQTT_PORT <= port <= MAX_MQTT_PORT:
            raise ConfigError(f"weintek.mqtt.port must be an integer in {MIN_MQTT_PORT}-{MAX_MQTT_PORT}")
        topics = _unique_tokens(
            entry.get("topics"),
            label="weintek.mqtt.topics",
            max_length=MAX_TOPIC_LENGTH,
            extra_chars="+#",
        )
        mqtt_keys.append((host, port))
        mqtt.append(WeintekMqttTarget(host=host, port=port, topics=topics, security=security))
    if len(set(mqtt_keys)) != len(mqtt_keys):
        raise ConfigError("weintek.mqtt host and port pairs must be unique")
    return allow, tuple(opcua), tuple(mqtt)


def _sketch_roots(raw: object) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ConfigError("flash.sketch_roots must be a list of directories")
    roots: list[str] = []
    for value in raw:
        if not isinstance(value, str) or not value or "\x00" in value:
            raise ConfigError("flash.sketch_roots entries must be non-empty paths")
        if any(char in value for char in ("\n", "\r")):
            raise ConfigError("flash.sketch_roots entries must be single-line paths")
        roots.append(value)
    if len(set(roots)) != len(roots):
        raise ConfigError("flash.sketch_roots must not contain duplicates")
    return tuple(roots)


def _check_permissions_stat(path: Path, st: os.stat_result) -> None:
    if not stat.S_ISREG(st.st_mode):
        raise ConfigError(f"{path} must be a regular file, not a symlink or directory")
    if st.st_uid != os.getuid():
        raise ConfigError(f"{path} must be owned by the current user")
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ConfigError(
            f"{path} is group- or world-accessible; it names the hosts this plugin may "
            f"reach over SSH. Run: chmod 600 {path}"
        )


def _check_permissions(path: Path) -> None:
    _check_permissions_stat(path, path.lstat())


def load() -> Config:
    # Open with O_NOFOLLOW so a symlink cannot replace the file between the
    # permission check and the read (classic TOCTOU).
    try:
        fd = os.open(CONFIG_PATH, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return Config()
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            return Config()
        if exc.errno == errno.ELOOP:
            raise ConfigError(f"{CONFIG_PATH} must be a regular file, not a symlink or directory") from exc
        raise ConfigError(f"Cannot read {CONFIG_PATH}: {exc}") from exc

    try:
        _check_permissions_stat(CONFIG_PATH, os.fstat(fd))
        with os.fdopen(fd, "rb") as handle:
            fd = -1  # ownership transferred to fdopen
            raw = tomllib.load(handle)
    finally:
        if fd >= 0:
            os.close(fd)

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

    jetson = raw.get("jetson", {})
    if not isinstance(jetson, dict):
        raise ConfigError("jetson must be a table")
    jetson_hosts = jetson.get("hosts", [])
    if (
        not isinstance(jetson_hosts, list)
        or not all(_valid_host(host) for host in jetson_hosts)
        or len(set(jetson_hosts)) != len(jetson_hosts)
    ):
        raise ConfigError(
            "jetson.hosts must contain unique hostnames or addresses without whitespace, "
            "paths, leading dashes, or control characters"
        )
    overlap = set(hosts) & set(jetson_hosts)
    if overlap:
        raise ConfigError("a host cannot be listed under both [pi] hosts and [jetson] hosts")

    ssh_timeout = pi.get("ssh_timeout", 10)
    actuation_budget = pi.get("actuation_budget_per_min", 120)
    max_write_bytes = serial.get("max_write_bytes", 4096)
    write_timeout_ms = serial.get("write_timeout_ms", 2_000)
    write_budget_bytes_per_min = serial.get("write_budget_bytes_per_min", 65536)
    allow_flash = flash.get("allow", False)
    sketch_roots = _sketch_roots(flash.get("sketch_roots", []))
    allow_unknown_serial = serial.get("allow_unknown", False)
    if (
        not isinstance(ssh_timeout, int)
        or isinstance(ssh_timeout, bool)
        or not MIN_SSH_TIMEOUT <= ssh_timeout <= MAX_SSH_TIMEOUT
    ):
        raise ConfigError(f"pi.ssh_timeout must be an integer in {MIN_SSH_TIMEOUT}-{MAX_SSH_TIMEOUT}")
    if (
        not isinstance(actuation_budget, int)
        or isinstance(actuation_budget, bool)
        or not MIN_ACTUATION_BUDGET <= actuation_budget <= MAX_ACTUATION_BUDGET
    ):
        raise ConfigError(
            f"pi.actuation_budget_per_min must be an integer in {MIN_ACTUATION_BUDGET}-{MAX_ACTUATION_BUDGET}"
        )
    if (
        not isinstance(max_write_bytes, int)
        or isinstance(max_write_bytes, bool)
        or not MIN_WRITE_BYTES <= max_write_bytes <= MAX_WRITE_BYTES
    ):
        raise ConfigError(f"serial.max_write_bytes must be an integer in {MIN_WRITE_BYTES}-{MAX_WRITE_BYTES}")
    if (
        not isinstance(write_timeout_ms, int)
        or isinstance(write_timeout_ms, bool)
        or not MIN_WRITE_TIMEOUT_MS <= write_timeout_ms <= MAX_WRITE_TIMEOUT_MS
    ):
        raise ConfigError(
            f"serial.write_timeout_ms must be an integer in {MIN_WRITE_TIMEOUT_MS}-{MAX_WRITE_TIMEOUT_MS}"
        )
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
    if not isinstance(allow_unknown_serial, bool):
        raise ConfigError("serial.allow_unknown must be a boolean")
    if allow_flash and not sketch_roots:
        raise ConfigError(
            "flash.allow is true but flash.sketch_roots is empty; "
            "add at least one allowed sketch directory"
        )

    weintek_allow, weintek_opcua, weintek_mqtt = _parse_weintek(raw)

    return Config(
        pi_hosts=tuple(hosts),
        jetson_hosts=tuple(jetson_hosts),
        pi_allowed_pins=tuple(pins),
        pi_ssh_timeout=ssh_timeout,
        actuation_budget_per_min=actuation_budget,
        max_write_bytes=max_write_bytes,
        write_timeout_ms=write_timeout_ms,
        write_budget_bytes_per_min=write_budget_bytes_per_min,
        allow_unknown_serial=allow_unknown_serial,
        allow_flash=allow_flash,
        sketch_roots=sketch_roots,
        weintek_allow=weintek_allow,
        weintek_opcua=weintek_opcua,
        weintek_mqtt=weintek_mqtt,
    )
