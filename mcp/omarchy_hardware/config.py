"""User configuration, read from ~/.config/omarchy-hardware/config.toml."""

from __future__ import annotations

import errno
import ipaddress
import os
import re
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
MING_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
MING_NODE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
MIN_MING_TIMEOUT = 1
MAX_MING_TIMEOUT = 30
MIN_MING_PAYLOAD = 1
MAX_MING_PAYLOAD = 65536
LOOPBACK_NAMES = frozenset({"localhost", "localhost.localdomain"})


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
    # A mode-600 file holding the password, as for MQTT: Claude Code starts the MCP
    # server, so an environment variable has to be exported into Claude Code itself.
    password_file: str | None = None


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
    # A mode-600 file holding the password, as an alternative to password_env.
    # Claude Code starts the MCP server, so an environment variable has to be
    # exported into Claude Code's own environment -- a file is often simpler.
    password_file: str | None = None


@dataclass(frozen=True)
class HttpSecurity:
    """How an HTTP API in the MING stack is reached and authenticated.

    HTTPS unless the target is loopback or `allow_insecure` is set. The token is
    named -- an environment variable or a mode-600 file -- never stored here.
    """

    ca_file: str | None = None
    token_env: str | None = None
    token_file: str | None = None
    allow_insecure: bool = False


@dataclass(frozen=True)
class MingMqttBroker:
    """An MQTT broker. `subscribe` holds topic filters, `publish` exact topics."""

    name: str
    host: str
    port: int
    subscribe: tuple[str, ...] = ()
    publish: tuple[str, ...] = ()
    security: MqttSecurity = MqttSecurity()


@dataclass(frozen=True)
class MingInfluxDb:
    name: str
    url: str
    org: str
    read_buckets: tuple[str, ...] = ()
    write_buckets: tuple[str, ...] = ()
    security: HttpSecurity = HttpSecurity()


@dataclass(frozen=True)
class MingNodeRed:
    name: str
    url: str
    inject_nodes: tuple[str, ...] = ()
    security: HttpSecurity = HttpSecurity()


@dataclass(frozen=True)
class MingGrafana:
    name: str
    url: str
    annotate: bool = False
    security: HttpSecurity = HttpSecurity()


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


# EasyBuilder Pro's MODBUS Server driver maps HMI memory onto Modbus tables:
# LB bits are coils (0x), LW words holding registers 4x 1-9999, RW words 4x from 10000.
MODBUS_AREAS = {"LB": 12_800, "LW": 9_999, "RW": 55_536}
MODBUS_RANGE = re.compile(r"^(LB|LW|RW)-(\d{1,5})(?::(\d{1,4}))?$")


@dataclass(frozen=True)
class ModbusRange:
    area: str
    start: int
    count: int

    def covers(self, area: str, start: int, count: int) -> bool:
        return area == self.area and self.start <= start and start + count <= self.start + self.count


@dataclass(frozen=True)
class WeintekModbusTarget:
    """An HMI running EasyBuilder Pro's MODBUS Server driver over Ethernet.

    Modbus TCP has no authentication and no encryption, so a target is only
    accepted with allow_insecure = true, and only reads are offered.
    """

    host: str
    port: int
    unit: int
    read: tuple[ModbusRange, ...]


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
    allow_fingerprinted: bool = False
    max_uploads_per_hour: int = 30
    weintek_allow: bool = False
    weintek_opcua: tuple[WeintekOpcUaTarget, ...] = ()
    weintek_mqtt: tuple[WeintekMqttTarget, ...] = ()
    weintek_modbus: tuple[WeintekModbusTarget, ...] = ()
    ming_allow: bool = False
    ming_timeout: int = 10
    ming_max_payload_bytes: int = 4096
    ming_write_budget_per_min: int = 60
    ming_mqtt: tuple[MingMqttBroker, ...] = ()
    ming_influxdb: tuple[MingInfluxDb, ...] = ()
    ming_nodered: tuple[MingNodeRed, ...] = ()
    ming_grafana: tuple[MingGrafana, ...] = ()


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
    try:
        parsed = urlparse(value)
    except ValueError as exc:
        raise ConfigError(f"weintek.opcua.endpoint is not a valid URL: {exc}") from exc
    if parsed.scheme != "opc.tcp" or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigError("weintek.opcua.endpoint must be opc.tcp://host:port with no credentials or query")
    if parsed.path not in ("", "/"):
        raise ConfigError("weintek.opcua.endpoint must not include a path")
    host = parsed.hostname
    if host is None or not _valid_host(host):
        raise ConfigError("weintek.opcua.endpoint host is not an allowed hostname or address")
    try:
        port = parsed.port or DEFAULT_OPCUA_PORT
    except ValueError as exc:
        raise ConfigError("weintek.opcua.endpoint has an invalid port") from exc
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
    trust_list = _optional_path(raw, "trust_list", label)
    if mode != "None" and not trust_list:
        # policy.py refuses the same target at call time; saying so at load time
        # points at the config instead of failing every OPC UA tool.
        raise ConfigError(
            f"{label}.trust_list is required unless mode is 'None': "
            "it pins the HMI's own server certificate"
        )

    password_env = _optional_name(raw, "password_env", label)
    password_file = _optional_path(raw, "password_file", label)
    if password_env and password_file:
        raise ConfigError(f"{label}.password_env and {label}.password_file are alternatives; set one")
    if (raw.get("username") is None) != (password_env is None and password_file is None):
        raise ConfigError(f"{label}.username and {label}.password_env (or password_file) must be set together")

    return OpcUaSecurity(
        policy=policy,
        mode=mode,
        certificate=certificate,
        private_key=private_key,
        trust_list=trust_list,
        username=_optional_name(raw, "username", label),
        password_env=password_env,
        allow_insecure=allow_insecure,
        password_file=password_file,
    )


def is_loopback(host: str) -> bool:
    """True for names and literals that never leave this machine."""
    if host.lower() in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _mqtt_security(entry: dict, label: str = "weintek.mqtt.security", *, loopback: bool = False) -> MqttSecurity:
    raw = entry.get("security", {})
    if not isinstance(raw, dict):
        raise ConfigError(f"{label} must be a table")

    tls = _flag(raw, "tls", label, True)
    allow_insecure = _flag(raw, "allow_insecure", label, False)
    # Cleartext to 127.0.0.1 never crosses a wire, so a broker on this machine
    # does not need the waiver. Every other address does.
    if not tls and not allow_insecure and not loopback:
        raise ConfigError(
            f"{label}.tls is false, which sends MQTT traffic in cleartext. "
            f"Set {label}.allow_insecure = true to accept that explicitly."
        )

    client_certificate = _optional_path(raw, "client_certificate", label)
    client_key = _optional_path(raw, "client_key", label)
    if bool(client_certificate) != bool(client_key):
        raise ConfigError(f"{label}.client_certificate and {label}.client_key must be set together")
    password_env = _optional_name(raw, "password_env", label)
    password_file = _optional_path(raw, "password_file", label)
    if password_env and password_file:
        raise ConfigError(f"{label}.password_env and {label}.password_file are alternatives; set one")
    if (raw.get("username") is None) != (password_env is None and password_file is None):
        raise ConfigError(f"{label}.username and {label}.password_env (or password_file) must be set together")

    return MqttSecurity(
        tls=tls,
        ca_file=_optional_path(raw, "ca_file", label),
        client_certificate=client_certificate,
        client_key=client_key,
        username=_optional_name(raw, "username", label),
        password_env=password_env,
        allow_insecure=allow_insecure,
        password_file=password_file,
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


def _modbus_range(value: object) -> ModbusRange:
    match = MODBUS_RANGE.match(value) if isinstance(value, str) else None
    if not match:
        raise ConfigError(f"weintek.modbus.read entry {value!r} must look like 'LW-100' or 'LW-100:16' (LB, LW or RW)")
    area, start, count = match.group(1), int(match.group(2)), int(match.group(3) or 1)
    if count < 1 or start + count > MODBUS_AREAS[area]:
        raise ConfigError(f"weintek.modbus.read entry {value!r} runs past the end of {area} ({MODBUS_AREAS[area]})")
    return ModbusRange(area, start, count)


def _parse_weintek_modbus(raw: dict) -> tuple[WeintekModbusTarget, ...]:
    weintek = raw.get("weintek", {})
    entries = weintek.get("modbus", []) if isinstance(weintek, dict) else []
    if not isinstance(entries, list):
        raise ConfigError("weintek.modbus must be an array of tables")
    targets: list[WeintekModbusTarget] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ConfigError("weintek.modbus entries must be tables")
        host = entry.get("host")
        if not _valid_host(host):
            raise ConfigError("weintek.modbus.host must be a hostname or address without paths or leading dashes")
        if entry.get("allow_insecure") is not True:
            raise ConfigError(
                "weintek.modbus has no authentication or encryption; "
                "set allow_insecure = true on the entry to accept that explicitly"
            )
        port = entry.get("port", 502)
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ConfigError("weintek.modbus.port must be an integer in 1-65535")
        unit = entry.get("unit", 1)
        if not isinstance(unit, int) or isinstance(unit, bool) or not 0 <= unit <= 255:
            raise ConfigError("weintek.modbus.unit must be an integer in 0-255")
        read = entry.get("read")
        if not isinstance(read, list) or not read:
            raise ConfigError("weintek.modbus.read must be a non-empty list such as [\"LW-0:16\"]")
        targets.append(WeintekModbusTarget(host, port, unit, tuple(_modbus_range(item) for item in read)))
    if len({(t.host, t.port) for t in targets}) != len(targets):
        raise ConfigError("weintek.modbus host and port pairs must be unique")
    return tuple(targets)


# --------------------------------------------------------------------------- MING stack


@dataclass(frozen=True)
class _Ming:
    allow: bool = False
    timeout: int = 10
    max_payload_bytes: int = 4096
    write_budget_per_min: int = 60
    mqtt: tuple[MingMqttBroker, ...] = ()
    influxdb: tuple[MingInfluxDb, ...] = ()
    nodered: tuple[MingNodeRed, ...] = ()
    grafana: tuple[MingGrafana, ...] = ()


def _bounded_int(table: dict, key: str, label: str, default: int, low: int, high: int) -> int:
    value = table.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise ConfigError(f"{label}.{key} must be an integer in {low}-{high}")
    return value


def _ming_name(entry: dict, label: str) -> str:
    name = entry.get("name")
    if not isinstance(name, str) or not MING_NAME.match(name):
        raise ConfigError(
            f"{label}.name must be 1-32 lowercase letters, digits, '-' or '_'; tools use it to pick the target"
        )
    return name


def _optional_tokens(values: object, *, label: str, max_length: int, extra_chars: str = "") -> tuple[str, ...]:
    if values is None or values == []:
        return ()
    return _unique_tokens(values, label=label, max_length=max_length, extra_chars=extra_chars)


def valid_topic_filter(value: str) -> bool:
    """MQTT 3.1.1 section 4.7: '#' alone and last, '+' only as a whole level."""
    levels = value.split("/")
    for index, level in enumerate(levels):
        if "#" in level and (level != "#" or index != len(levels) - 1):
            return False
        if "+" in level and level != "+":
            return False
    return True


def _topic_filters(values: object, label: str) -> tuple[str, ...]:
    filters = _optional_tokens(values, label=label, max_length=MAX_TOPIC_LENGTH)
    for value in filters:
        if not valid_topic_filter(value):
            raise ConfigError(f"{label} entry {value!r} places '+' or '#' where MQTT does not allow them")
    return filters


def _publish_topics(values: object, label: str) -> tuple[str, ...]:
    topics = _optional_tokens(values, label=label, max_length=MAX_TOPIC_LENGTH, extra_chars="+#")
    if any(topic.startswith("$") for topic in topics):
        raise ConfigError(f"{label} must not name broker-reserved '$' topics")
    return topics


def _http_url(value: object, label: str) -> tuple[str, str, bool]:
    """Normalise an http(s) base URL; return it with its host and whether it is HTTPS."""
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{label}.url must be an http:// or https:// URL")
    try:
        parsed = urlparse(value)
    except ValueError as exc:
        raise ConfigError(f"{label}.url is not a valid URL: {exc}") from exc
    if parsed.scheme not in ("http", "https"):
        raise ConfigError(f"{label}.url must be an http:// or https:// URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.params:
        raise ConfigError(f"{label}.url must not carry credentials, a query or a fragment")
    host = parsed.hostname
    if host is None or not _valid_host(host):
        raise ConfigError(f"{label}.url host is not an allowed hostname or address")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ConfigError(f"{label}.url has an invalid port") from exc
    path = parsed.path.rstrip("/")
    if any(char.isspace() or not char.isprintable() for char in path) or ".." in path.split("/"):
        raise ConfigError(f"{label}.url path must be a plain prefix")
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc = f"{netloc}:{port}"
    return f"{parsed.scheme}://{netloc}{path}", host, parsed.scheme == "https"


def _http_security(entry: dict, label: str, *, https: bool, loopback: bool) -> HttpSecurity:
    raw = entry.get("security", {})
    if not isinstance(raw, dict):
        raise ConfigError(f"{label}.security must be a table")
    slabel = f"{label}.security"
    allow_insecure = _flag(raw, "allow_insecure", slabel, False)
    if not https and not loopback and not allow_insecure:
        raise ConfigError(
            f"{label}.url is http:// to another machine, which sends the API token in cleartext. "
            f"Use https://, or set {slabel}.allow_insecure = true to accept that explicitly."
        )
    token_env = _optional_name(raw, "token_env", slabel)
    token_file = _optional_path(raw, "token_file", slabel)
    if token_env and token_file:
        raise ConfigError(f"{slabel}.token_env and {slabel}.token_file are alternatives; set one")
    return HttpSecurity(
        ca_file=_optional_path(raw, "ca_file", slabel),
        token_env=token_env,
        token_file=token_file,
        allow_insecure=allow_insecure,
    )


def _ming_entries(ming: dict, key: str) -> list[dict]:
    entries = ming.get(key, [])
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        raise ConfigError(f"ming.{key} must be an array of tables")
    return entries


def _unique_names(items: tuple, label: str) -> None:
    names = [item.name for item in items]
    if len(set(names)) != len(names):
        raise ConfigError(f"{label} names must be unique")


def _parse_ming(raw: dict) -> _Ming:
    ming = raw.get("ming", {})
    if not ming:
        return _Ming()
    if not isinstance(ming, dict):
        raise ConfigError("ming must be a table")
    allow = _flag(ming, "allow", "ming", False)

    brokers: list[MingMqttBroker] = []
    for entry in _ming_entries(ming, "mqtt"):
        label = "ming.mqtt"
        name = _ming_name(entry, label)
        host = entry.get("host")
        if not _valid_host(host):
            raise ConfigError(f"{label}.host must be a hostname or address without paths or leading dashes")
        security = _mqtt_security(entry, f"{label}.security", loopback=is_loopback(host))
        port = _bounded_int(
            entry,
            "port",
            label,
            DEFAULT_MQTT_TLS_PORT if security.tls else DEFAULT_MQTT_PORT,
            MIN_MQTT_PORT,
            MAX_MQTT_PORT,
        )
        brokers.append(
            MingMqttBroker(
                name=name,
                host=host,
                port=port,
                subscribe=_topic_filters(entry.get("subscribe"), f"{label}.subscribe"),
                publish=_publish_topics(entry.get("publish"), f"{label}.publish"),
                security=security,
            )
        )

    influxdbs: list[MingInfluxDb] = []
    for entry in _ming_entries(ming, "influxdb"):
        label = "ming.influxdb"
        url, host, https = _http_url(entry.get("url"), label)
        org = entry.get("org")
        if not isinstance(org, str) or not org or len(org) > 64 or not org.isprintable():
            raise ConfigError(f"{label}.org must be a printable organisation name of at most 64 characters")
        influxdbs.append(
            MingInfluxDb(
                name=_ming_name(entry, label),
                url=url,
                org=org,
                read_buckets=_optional_tokens(entry.get("read_buckets"), label=f"{label}.read_buckets", max_length=64),
                write_buckets=_optional_tokens(
                    entry.get("write_buckets"), label=f"{label}.write_buckets", max_length=64
                ),
                security=_http_security(entry, label, https=https, loopback=is_loopback(host)),
            )
        )

    noderes: list[MingNodeRed] = []
    for entry in _ming_entries(ming, "nodered"):
        label = "ming.nodered"
        url, host, https = _http_url(entry.get("url"), label)
        inject_nodes = _optional_tokens(entry.get("inject_nodes"), label=f"{label}.inject_nodes", max_length=64)
        if not all(MING_NODE_ID.match(node) for node in inject_nodes):
            raise ConfigError(f"{label}.inject_nodes must be Node-RED node ids (letters, digits, '.', '_', '-')")
        noderes.append(
            MingNodeRed(
                name=_ming_name(entry, label),
                url=url,
                inject_nodes=inject_nodes,
                security=_http_security(entry, label, https=https, loopback=is_loopback(host)),
            )
        )

    grafanas: list[MingGrafana] = []
    for entry in _ming_entries(ming, "grafana"):
        label = "ming.grafana"
        url, host, https = _http_url(entry.get("url"), label)
        grafanas.append(
            MingGrafana(
                name=_ming_name(entry, label),
                url=url,
                annotate=_flag(entry, "annotate", label, False),
                security=_http_security(entry, label, https=https, loopback=is_loopback(host)),
            )
        )

    parsed = _Ming(
        allow=allow,
        timeout=_bounded_int(ming, "timeout", "ming", 10, MIN_MING_TIMEOUT, MAX_MING_TIMEOUT),
        max_payload_bytes=_bounded_int(
            ming, "max_payload_bytes", "ming", 4096, MIN_MING_PAYLOAD, MAX_MING_PAYLOAD
        ),
        write_budget_per_min=_bounded_int(
            ming, "write_budget_per_min", "ming", 60, MIN_ACTUATION_BUDGET, MAX_ACTUATION_BUDGET
        ),
        mqtt=tuple(brokers),
        influxdb=tuple(influxdbs),
        nodered=tuple(noderes),
        grafana=tuple(grafanas),
    )
    for items, label in (
        (parsed.mqtt, "ming.mqtt"),
        (parsed.influxdb, "ming.influxdb"),
        (parsed.nodered, "ming.nodered"),
        (parsed.grafana, "ming.grafana"),
    ):
        _unique_names(items, label)
    return parsed


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


def read_raw() -> dict | None:
    """The parsed config.toml after the ownership and permission checks, or None if absent."""
    # Open with O_NOFOLLOW so a symlink cannot replace the file between the
    # permission check and the read (classic TOCTOU).
    try:
        fd = os.open(CONFIG_PATH, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            return None
        if exc.errno == errno.ELOOP:
            raise ConfigError(f"{CONFIG_PATH} must be a regular file, not a symlink or directory") from exc
        raise ConfigError(f"Cannot read {CONFIG_PATH}: {exc}") from exc

    try:
        _check_permissions_stat(CONFIG_PATH, os.fstat(fd))
        with os.fdopen(fd, "rb") as handle:
            fd = -1  # ownership transferred to fdopen
            return tomllib.load(handle)
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{CONFIG_PATH} is not valid TOML: {exc}") from exc
    finally:
        if fd >= 0:
            os.close(fd)


def load() -> Config:
    raw = read_raw()
    if raw is None:
        return Config()

    pi = raw.get("pi", {})
    serial = raw.get("serial", {})
    flash = raw.get("flash", {})
    for name, section in (("pi", pi), ("serial", serial), ("flash", flash)):
        if not isinstance(section, dict):
            raise ConfigError(f"{name} must be a table")

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
    allow_fingerprinted = flash.get("allow_fingerprinted", False)
    max_uploads_per_hour = flash.get("max_uploads_per_hour", 30)
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
    if not isinstance(allow_fingerprinted, bool):
        raise ConfigError("flash.allow_fingerprinted must be a boolean")
    if (
        not isinstance(max_uploads_per_hour, int)
        or isinstance(max_uploads_per_hour, bool)
        or not 1 <= max_uploads_per_hour <= 1000
    ):
        raise ConfigError("flash.max_uploads_per_hour must be an integer in 1-1000")
    if not isinstance(allow_unknown_serial, bool):
        raise ConfigError("serial.allow_unknown must be a boolean")
    if allow_flash and not sketch_roots:
        raise ConfigError(
            "flash.allow is true but flash.sketch_roots is empty; "
            "add at least one allowed sketch directory"
        )

    weintek_allow, weintek_opcua, weintek_mqtt = _parse_weintek(raw)
    weintek_modbus = _parse_weintek_modbus(raw)
    ming = _parse_ming(raw)

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
        allow_fingerprinted=allow_fingerprinted,
        max_uploads_per_hour=max_uploads_per_hour,
        weintek_allow=weintek_allow,
        weintek_opcua=weintek_opcua,
        weintek_mqtt=weintek_mqtt,
        weintek_modbus=weintek_modbus,
        ming_allow=ming.allow,
        ming_timeout=ming.timeout,
        ming_max_payload_bytes=ming.max_payload_bytes,
        ming_write_budget_per_min=ming.write_budget_per_min,
        ming_mqtt=ming.mqtt,
        ming_influxdb=ming.influxdb,
        ming_nodered=ming.nodered,
        ming_grafana=ming.grafana,
    )
