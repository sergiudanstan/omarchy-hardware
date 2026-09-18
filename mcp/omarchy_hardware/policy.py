"""The single choke point for every privileged or destructive parameter.

No tool touches a device path, an SSH host, or a GPIO pin without passing through
here first.
"""

from __future__ import annotations

import grp
import os
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from . import errors
from .config import (
    DEFAULT_MQTT_TLS_PORT,
    Config,
    HttpSecurity,
    MingGrafana,
    MingInfluxDb,
    MingMqttBroker,
    MingNodeRed,
    WeintekMqttTarget,
    WeintekOpcUaTarget,
    is_loopback,
)
from .errors import ToolError
from .mqtt_lite import filter_covers

# Only USB-attached serial adapters. /dev/ttyS* is deliberately excluded: those are
# built-in UARTs, which on many machines are serial consoles.
DEVICE_PATTERN = re.compile(r"^/dev/tty(ACM|USB)\d+$")
BY_ID_PREFIX = "/dev/serial/by-id/"

SERIAL_GROUPS = ("uucp", "dialout")


def resolve_port(port: str) -> str:
    """Validate a caller-supplied port, before and after symlink resolution."""
    if not isinstance(port, str) or "\x00" in port:
        raise ToolError(errors.PORT_NOT_ALLOWED, "Port must be a plain string.")

    if not (DEVICE_PATTERN.match(port) or port.startswith(BY_ID_PREFIX)):
        raise ToolError(
            errors.PORT_NOT_ALLOWED,
            f"Refusing to open {port!r}.",
            "Only /dev/ttyACM*, /dev/ttyUSB* and /dev/serial/by-id/* are permitted.",
        )

    if port.startswith(BY_ID_PREFIX) and (".." in port or "/" in port[len(BY_ID_PREFIX):]):
        raise ToolError(errors.PORT_NOT_ALLOWED, f"Refusing to open {port!r}.", "Path traversal is not permitted.")

    resolved = os.path.realpath(port)

    # Re-check after resolution: the input may have been a symlink pointing elsewhere.
    if not DEVICE_PATTERN.match(resolved):
        raise ToolError(
            errors.PORT_NOT_ALLOWED,
            f"{port!r} resolves to {resolved!r}, which is not a USB serial device.",
            "Only /dev/ttyACM* and /dev/ttyUSB* are permitted.",
        )

    if not Path(resolved).exists():
        raise ToolError(
            errors.PORT_NOT_FOUND, f"{resolved} is not connected.", "Run list_boards to see what is attached."
        )

    return resolved


def check_readable(port: str) -> None:
    if os.access(port, os.R_OK | os.W_OK):
        return

    user_groups = {grp.getgrgid(gid).gr_name for gid in os.getgroups()}
    missing = [g for g in SERIAL_GROUPS if _group_exists(g) and g not in user_groups]
    hint = (
        f"You are not in the '{missing[0]}' group. Run the plugin's bin/setup.sh, then log out and back in."
        if missing
        else "Check the device permissions."
    )
    raise ToolError(errors.PERMISSION_DENIED_UUCP, f"No read/write access to {port}.", hint)


def _group_exists(name: str) -> bool:
    try:
        grp.getgrnam(name)
        return True
    except KeyError:
        return False


def check_host(
    host: str,
    config: Config,
    *,
    hosts: tuple[str, ...] | None = None,
    section: str = "[pi] hosts",
) -> str:
    allowed = config.pi_hosts if hosts is None else hosts
    if not allowed:
        raise ToolError(
            errors.HOST_NOT_ALLOWED,
            "No hosts are configured for this family.",
            f"Add the host to {section} in ~/.config/omarchy-hardware/config.toml.",
        )
    if host not in allowed:
        raise ToolError(
            errors.HOST_NOT_ALLOWED,
            f"Host {host!r} is not in the allowlist.",
            f"Add it to {section} in ~/.config/omarchy-hardware/config.toml.",
        )
    return host


def check_weintek_enabled(config: Config) -> None:
    if not config.weintek_allow:
        raise ToolError(
            errors.HOST_NOT_ALLOWED,
            "Weintek OPC UA and MQTT are disabled.",
            "Set [weintek] allow = true in ~/.config/omarchy-hardware/config.toml after allowlisting endpoints.",
        )


def check_weintek_opcua(config: Config, endpoint: str, node: str) -> WeintekOpcUaTarget:
    """Authorise one OPC UA node and hand back the security context to use.

    The target is returned rather than just approved so a client cannot connect
    without the settings that were validated here: there is no code path that
    yields an authorised endpoint and no session security to apply to it.
    """
    check_weintek_enabled(config)
    for target in config.weintek_opcua:
        if target.endpoint == endpoint and node in target.nodes:
            _require_secure_opcua(target)
            return target
    raise ToolError(
        errors.HOST_NOT_ALLOWED,
        f"OPC UA {endpoint!r} node {node!r} is not in the Weintek allowlist.",
        "Add the endpoint and exact node id under [[weintek.opcua]] in config.toml.",
    )


def _require_secure_opcua(target: WeintekOpcUaTarget) -> None:
    security = target.security
    if security.mode == "None" and not security.allow_insecure:
        raise ToolError(
            errors.INSECURE_TRANSPORT,
            "This OPC UA endpoint would be reached with no signing and no encryption.",
            "Set a security mode under [[weintek.opcua]].security, or allow_insecure = true to accept it.",
        )
    if security.mode != "None" and not (security.certificate and security.private_key):
        raise ToolError(
            errors.INSECURE_TRANSPORT,
            "This OPC UA endpoint has a security mode but no client certificate.",
            "Set certificate and private_key under [[weintek.opcua]].security.",
        )


def check_weintek_mqtt(config: Config, host: str, topic: str, port: int = DEFAULT_MQTT_TLS_PORT) -> WeintekMqttTarget:
    """Authorise one MQTT topic and hand back the security context to use."""
    check_weintek_enabled(config)
    for target in config.weintek_mqtt:
        if target.host == host and target.port == port and topic in target.topics:
            if not target.security.tls and not target.security.allow_insecure:
                raise ToolError(
                    errors.INSECURE_TRANSPORT,
                    "This MQTT target would be published to in cleartext.",
                    "Set tls = true under [[weintek.mqtt]].security, or allow_insecure = true to accept it.",
                )
            return target
    raise ToolError(
        errors.HOST_NOT_ALLOWED,
        f"MQTT {host!r}:{port} topic {topic!r} is not in the Weintek allowlist.",
        "Add the host, port, and exact topic under [[weintek.mqtt]] in config.toml.",
    )


# --------------------------------------------------------------------------- MING stack

CONFIG_HINT = "~/.config/omarchy-hardware/config.toml"


def check_ming_enabled(config: Config) -> None:
    if not config.ming_allow:
        raise ToolError(
            errors.HOST_NOT_ALLOWED,
            "The MING stack tools are disabled.",
            f"Set [ming] allow = true in {CONFIG_HINT} after adding [[ming.*]] targets.",
        )


def _pick(targets: tuple, name: str | None, kind: str):  # noqa: ANN202 - returns one of the Ming* types
    """Resolve a target by its configured name; with one target, the name may be omitted."""
    if name is None:
        if len(targets) == 1:
            return targets[0]
        if not targets:
            raise ToolError(
                errors.HOST_NOT_ALLOWED,
                f"No {kind} targets are configured.",
                f"Add one under [[ming.{kind}]] in {CONFIG_HINT}.",
            )
        raise ToolError(
            errors.HOST_NOT_ALLOWED,
            f"Several {kind} targets are configured; name the one you mean.",
            f"Pass one of: {', '.join(target.name for target in targets)}.",
        )
    for target in targets:
        if target.name == name:
            return target
    raise ToolError(
        errors.HOST_NOT_ALLOWED,
        f"No {kind} target is named {name!r}.",
        f"Configured names: {', '.join(t.name for t in targets) or 'none'}. Add it under [[ming.{kind}]].",
    )


def _require_secure_http(url: str, security: HttpSecurity, label: str) -> None:
    # config.load already refuses this; re-checked because a Config can be built directly.
    parsed = urlparse(url)
    if parsed.scheme != "https" and not security.allow_insecure and not is_loopback(parsed.hostname or ""):
        raise ToolError(
            errors.INSECURE_TRANSPORT,
            f"{label} would be reached over cleartext HTTP, exposing its API token.",
            f"Use an https:// URL, or set allow_insecure = true under its security table in {CONFIG_HINT}.",
        )


def ming_mqtt(config: Config, name: str | None) -> MingMqttBroker:
    check_ming_enabled(config)
    broker: MingMqttBroker = _pick(config.ming_mqtt, name, "mqtt")
    security = broker.security
    if not security.tls and not security.allow_insecure and not is_loopback(broker.host):
        raise ToolError(
            errors.INSECURE_TRANSPORT,
            f"MQTT broker {broker.name!r} would be reached in cleartext.",
            "Set tls = true under its security table, or allow_insecure = true to accept it.",
        )
    return broker


def check_ming_subscribe(broker: MingMqttBroker, topic_filter: str) -> str:
    """The requested filter must be one of the configured filters or a narrowing of one."""
    if any(filter_covers(allowed, topic_filter) for allowed in broker.subscribe):
        return topic_filter
    raise ToolError(
        errors.HOST_NOT_ALLOWED,
        f"Topic filter {topic_filter!r} is not within broker {broker.name!r}'s subscribe allowlist.",
        f"Allowed filters: {', '.join(broker.subscribe) or 'none'}. Widen [[ming.mqtt]] subscribe to change that.",
    )


def check_ming_publish(broker: MingMqttBroker, topic: str) -> str:
    if topic in broker.publish:
        return topic
    raise ToolError(
        errors.HOST_NOT_ALLOWED,
        f"Topic {topic!r} is not in broker {broker.name!r}'s publish allowlist.",
        f"Allowed topics: {', '.join(broker.publish) or 'none'}. Publishing needs an exact topic under publish.",
    )


def ming_influxdb(config: Config, name: str | None) -> MingInfluxDb:
    check_ming_enabled(config)
    db: MingInfluxDb = _pick(config.ming_influxdb, name, "influxdb")
    _require_secure_http(db.url, db.security, f"InfluxDB {db.name!r}")
    return db


def check_ming_bucket(db: MingInfluxDb, bucket: str, *, write: bool) -> str:
    allowed = db.write_buckets if write else db.read_buckets
    if bucket in allowed:
        return bucket
    verb = "write" if write else "read"
    raise ToolError(
        errors.HOST_NOT_ALLOWED,
        f"Bucket {bucket!r} is not in InfluxDB {db.name!r}'s {verb} allowlist.",
        f"Allowed: {', '.join(allowed) or 'none'}. Add it to {verb}_buckets under [[ming.influxdb]].",
    )


def ming_nodered(config: Config, name: str | None) -> MingNodeRed:
    check_ming_enabled(config)
    nodered: MingNodeRed = _pick(config.ming_nodered, name, "nodered")
    _require_secure_http(nodered.url, nodered.security, f"Node-RED {nodered.name!r}")
    return nodered


def check_ming_inject(nodered: MingNodeRed, node_id: str) -> str:
    if node_id in nodered.inject_nodes:
        return node_id
    raise ToolError(
        errors.HOST_NOT_ALLOWED,
        f"Inject node {node_id!r} is not in Node-RED {nodered.name!r}'s allowlist.",
        "Add its id to inject_nodes under [[ming.nodered]]; nodered_flows lists inject node ids.",
    )


def ming_grafana(config: Config, name: str | None, *, annotate: bool = False) -> MingGrafana:
    check_ming_enabled(config)
    grafana: MingGrafana = _pick(config.ming_grafana, name, "grafana")
    _require_secure_http(grafana.url, grafana.security, f"Grafana {grafana.name!r}")
    if annotate and not grafana.annotate:
        raise ToolError(
            errors.HOST_NOT_ALLOWED,
            f"Annotations are not enabled for Grafana {grafana.name!r}.",
            "Set annotate = true under its [[ming.grafana]] entry.",
        )
    return grafana


def check_pin(bcm: int, config: Config) -> int:
    if not isinstance(bcm, int) or isinstance(bcm, bool):
        raise ToolError(errors.PIN_NOT_ALLOWED, "Pin must be an integer BCM number.")
    if bcm not in config.pi_allowed_pins:
        raise ToolError(
            errors.PIN_NOT_ALLOWED,
            f"BCM pin {bcm} is not in the allowlist.",
            "Adjust [pi] allowed_pins in ~/.config/omarchy-hardware/config.toml.",
        )
    return bcm


class _RollingBudget:
    """Rolling one-minute cap, counted per target."""

    unit = "units"
    noun = "Budget"
    advice = "Wait a moment before trying again."

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._events: dict[str, list[tuple[float, int]]] = {}
        self._lock = threading.Lock()

    def charge(self, target: str, count: int = 1) -> None:
        now = time.monotonic()
        with self._lock:
            events = [(t, n) for t, n in self._events.get(target, []) if now - t < 60.0]
            spent = sum(n for _, n in events)

            if spent + count > self.limit:
                raise ToolError(
                    errors.RATE_LIMITED,
                    f"{self.noun} exhausted for {target} "
                    f"({spent}/{self.limit} {self.unit} in the last minute).",
                    self.advice,
                )

            events.append((now, count))
            self._events[target] = events


class WriteBudget(_RollingBudget):
    """Caps bytes written per serial port."""

    unit = "bytes"
    noun = "Write budget"
    advice = "Wait a moment before writing again."


class ActuationBudget(_RollingBudget):
    """Caps state-changing operations per remote target.

    A GPIO pin driven in a tight loop is not a data-volume problem, so bytes are
    the wrong unit: what wears a relay or a contactor is the number of
    transitions. Counted per host and pin so one runaway pin cannot starve the
    rest of the board.
    """

    unit = "operations"
    noun = "Actuation budget"
    advice = "Wait a moment before driving this pin again."


class MingWriteBudget(_RollingBudget):
    """Caps writes per MING target: a publish, a point batch, an inject, an annotation.

    Counted per destination (a topic, a bucket, a node) for the same reason as
    ActuationBudget: one runaway loop must not starve the rest of the stack.
    """

    unit = "writes"
    noun = "MING write budget"
    advice = "Wait a moment before writing to this target again."
