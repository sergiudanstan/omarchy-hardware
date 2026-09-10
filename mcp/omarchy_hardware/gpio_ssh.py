"""Raspberry Pi GPIO over SSH.

There is deliberately no "run a command on the Pi" tool. Only fixed `pinctrl` /
`raspi-gpio` verbs are ever constructed, from an argv list with shell=False, using a
pin number that has already been validated against the configured allowlist.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from typing import Any

from . import errors
from .config import Config
from .errors import ToolError

SSH_BASE = (
    "ssh",
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=accept-new",
    "-T",
)

# pinctrl:   "17: op dh | hi // GPIO17 = output"
PINCTRL_LINE = re.compile(
    r"^\s*(?P<bcm>\d+):\s*(?P<mode>\S+)(?:\s+(?P<pull>p[udn]))?\s*\|\s*(?P<level>hi|lo)\s*(?://\s*(?P<name>.*))?$"
)
# raspi-gpio: "GPIO 17: level=0 fsel=0 func=INPUT"
RASPI_LINE = re.compile(
    r"^\s*GPIO\s+(?P<bcm>\d+):\s*level=(?P<level>[01])\s+fsel=\d+(?:\s+alt=\S+)?\s+func=(?P<func>\S+)"
)

_BACKENDS: dict[str, str] = {}


def _run(host: str, argv: list[str], config: Config) -> subprocess.CompletedProcess:
    command = " ".join(shlex.quote(part) for part in argv)
    full = [*SSH_BASE, "-o", f"ConnectTimeout={config.pi_ssh_timeout}", host, "--", command]
    try:
        return subprocess.run(
            full,
            capture_output=True,
            text=True,
            timeout=config.pi_ssh_timeout + 5,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError(errors.SSH_FAILED, f"SSH to {host} timed out.", "Check the Pi is powered and reachable.") from exc
    except FileNotFoundError as exc:
        raise ToolError(errors.TOOL_MISSING, "The ssh client is not installed.", "Install openssh.") from exc


def _fail(host: str, result: subprocess.CompletedProcess) -> ToolError:
    detail = (result.stderr or result.stdout or "").strip().splitlines()
    message = detail[0] if detail else f"exit status {result.returncode}"
    if "Permission denied" in message or "publickey" in message:
        return ToolError(
            errors.SSH_FAILED,
            f"SSH to {host} was refused: {message}",
            "Set up key-based login: ssh-copy-id " + host,
        )
    return ToolError(errors.SSH_FAILED, f"{host}: {message}")


def detect_backend(host: str, config: Config) -> str:
    if host in _BACKENDS:
        return _BACKENDS[host]

    for candidate in ("pinctrl", "raspi-gpio"):
        result = _run(host, ["command", "-v", candidate], config)
        if result.returncode == 0 and result.stdout.strip():
            _BACKENDS[host] = candidate
            return candidate

    raise ToolError(
        errors.TOOL_MISSING,
        f"Neither pinctrl nor raspi-gpio is available on {host}.",
        "Install raspi-utils (Pi OS Bookworm) or raspi-gpio on the Pi.",
    )


def _parse_pins(backend: str, text: str) -> list[dict[str, Any]]:
    pins: list[dict[str, Any]] = []
    pattern = PINCTRL_LINE if backend == "pinctrl" else RASPI_LINE

    for line in text.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        fields = match.groupdict()
        if backend == "pinctrl":
            pins.append(
                {
                    "bcm": int(fields["bcm"]),
                    "mode": fields["mode"],
                    "pull": fields.get("pull"),
                    "level": 1 if fields["level"] == "hi" else 0,
                    "name": (fields.get("name") or "").strip() or None,
                }
            )
        else:
            func = fields["func"].lower()
            pins.append(
                {
                    "bcm": int(fields["bcm"]),
                    "mode": "ip" if func == "input" else "op" if func == "output" else func,
                    "pull": None,
                    "level": int(fields["level"]),
                    "name": None,
                }
            )
    return pins


def list_pins(host: str, config: Config) -> list[dict[str, Any]]:
    backend = detect_backend(host, config)
    argv = [backend, "get"] if backend == "pinctrl" else [backend, "get"]
    result = _run(host, argv, config)
    if result.returncode != 0:
        raise _fail(host, result)
    return _parse_pins(backend, result.stdout)


def read_pin(host: str, bcm: int, config: Config) -> dict[str, Any]:
    backend = detect_backend(host, config)
    result = _run(host, [backend, "get", str(bcm)], config)
    if result.returncode != 0:
        raise _fail(host, result)

    pins = _parse_pins(backend, result.stdout)
    if not pins:
        raise ToolError(errors.SSH_FAILED, f"Could not parse pin state from {host}: {result.stdout.strip()[:200]}")
    return pins[0]


MODES = {
    "in": ["ip"],
    "out": ["op"],
    "pull_up": ["ip", "pu"],
    "pull_down": ["ip", "pd"],
    "none": ["ip", "pn"],
}


def set_mode(host: str, bcm: int, mode: str, config: Config) -> dict[str, Any]:
    if mode not in MODES:
        raise ToolError(errors.PIN_NOT_ALLOWED, f"Unknown mode {mode!r}.", f"Use one of: {', '.join(MODES)}.")

    backend = detect_backend(host, config)
    result = _run(host, [backend, "set", str(bcm), *MODES[mode]], config)
    if result.returncode != 0:
        raise _fail(host, result)
    return read_pin(host, bcm, config)


def write_pin(host: str, bcm: int, level: int, config: Config) -> dict[str, Any]:
    if level not in (0, 1):
        raise ToolError(errors.PIN_NOT_ALLOWED, "Level must be 0 or 1.")

    backend = detect_backend(host, config)
    previous = read_pin(host, bcm, config)
    result = _run(host, [backend, "set", str(bcm), "op", "dh" if level else "dl"], config)
    if result.returncode != 0:
        raise _fail(host, result)
    return {"previous_level": previous["level"], "pin": read_pin(host, bcm, config)}


def status(host: str, config: Config) -> dict[str, Any]:
    import time

    started = time.monotonic()
    result = _run(host, ["cat", "/proc/device-tree/model"], config)
    latency_ms = round((time.monotonic() - started) * 1000)

    if result.returncode != 0:
        raise _fail(host, result)

    return {
        "host": host,
        "reachable": True,
        "model": result.stdout.strip().strip("\x00") or None,
        "backend": detect_backend(host, config),
        "latency_ms": latency_ms,
    }
