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

from . import errors
from .config import Config
from .errors import ToolError

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


def check_weintek_opcua(config: Config, endpoint: str, node: str) -> None:
    check_weintek_enabled(config)
    for target in config.weintek_opcua:
        if target.endpoint == endpoint and node in target.nodes:
            return
    raise ToolError(
        errors.HOST_NOT_ALLOWED,
        f"OPC UA {endpoint!r} node {node!r} is not in the Weintek allowlist.",
        "Add the endpoint and exact node id under [[weintek.opcua]] in config.toml.",
    )


def check_weintek_mqtt(config: Config, host: str, topic: str, port: int = 1883) -> None:
    check_weintek_enabled(config)
    for target in config.weintek_mqtt:
        if target.host == host and target.port == port and topic in target.topics:
            return
    raise ToolError(
        errors.HOST_NOT_ALLOWED,
        f"MQTT {host!r}:{port} topic {topic!r} is not in the Weintek allowlist.",
        "Add the host, port, and exact topic under [[weintek.mqtt]] in config.toml.",
    )


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


class WriteBudget:
    """Rolling one-minute cap on bytes written per port."""

    def __init__(self, budget_bytes: int) -> None:
        self.limit = budget_bytes
        self._events: dict[str, list[tuple[float, int]]] = {}
        self._lock = threading.Lock()

    def charge(self, port: str, count: int) -> None:
        now = time.monotonic()
        with self._lock:
            events = [(t, n) for t, n in self._events.get(port, []) if now - t < 60.0]
            spent = sum(n for _, n in events)

            if spent + count > self.limit:
                raise ToolError(
                    errors.RATE_LIMITED,
                    f"Write budget exhausted for {port} ({spent}/{self.limit} bytes in the last minute).",
                    "Wait a moment before writing again.",
                )

            events.append((now, count))
            self._events[port] = events
