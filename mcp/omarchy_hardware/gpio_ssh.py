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

from . import errors, support
from .capabilities import CapabilityDevice
from .config import Config
from .errors import ToolError
from .policy import check_host

SSH_BASE = (
    "ssh",
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=yes",
    "-o", "ForwardAgent=no",
    "-o", "ForwardX11=no",
    "-o", "PermitLocalCommand=no",
    "-o", "ClearAllForwardings=yes",
    "-o", "ProxyCommand=none",
    "-T",
)

# pinctrl emits a variable number of flag tokens between the mode and the level,
# and uses "--" as a placeholder for an unset one. All of these are real:
#   "17: op dh | hi // GPIO17 = output"
#   "26: ip    -- | lo // GPIO26 = input"
#   "26: op -- -- | lo // GPIO26 = output"
#   "6: op dl pu | lo // GPIO6 = output"
#   "0: a3    pu | hi // ID_SDA/GPIO0 = SDA0"
# So capture the whole flag run and interpret it, rather than assuming one pull token.
PINCTRL_LINE = re.compile(
    r"^\s*(?P<bcm>\d+):\s*(?P<mode>\S+)(?P<flags>[^|]*)\|\s*(?P<level>hi|lo)\s*(?://\s*(?P<name>.*))?$"
)
PULLS = {"pu", "pd", "pn"}
DRIVES = {"dh", "dl"}
# raspi-gpio: "GPIO 17: level=0 fsel=0 func=INPUT"
RASPI_LINE = re.compile(
    r"^\s*GPIO\s+(?P<bcm>\d+):\s*level=(?P<level>[01])\s+fsel=\d+(?:\s+alt=\S+)?\s+func=(?P<func>\S+)"
)

_BACKENDS: dict[str, str] = {}
_TOOLS: dict[str, dict[str, bool]] = {}
TOOL_NAMES = ("pinctrl", "raspi-gpio", "vcgencmd")
NET_IFACES = ("eth0", "wlan0", "end0")
THROTTLED_BITS = (
    (0x1, "under_voltage"),
    (0x2, "arm_frequency_capped"),
    (0x4, "currently_throttled"),
    (0x8, "soft_temp_limit"),
    (0x10000, "under_voltage_occurred"),
    (0x20000, "arm_frequency_capped_occurred"),
    (0x40000, "throttling_occurred"),
    (0x80000, "soft_temp_limit_occurred"),
)


def _run(
    host: str,
    argv: list[str],
    config: Config,
    *,
    hosts: tuple[str, ...] | None = None,
    section: str = "[pi] hosts",
) -> subprocess.CompletedProcess:
    check_host(host, config, hosts=hosts, section=section)
    command = " ".join(shlex.quote(part) for part in argv)
    # `--` must precede the destination. OpenSSH treats `--` *after* the host as
    # the first word of the remote command, so `pinctrl` would never run.
    full = [*SSH_BASE, "-o", f"ConnectTimeout={config.pi_ssh_timeout}", "--", host, command]
    try:
        # S603: an argv list with shell=False. `host` has already passed
        # policy.check_host against the config allowlist, and `argv` is built only
        # from fixed verbs (pinctrl, raspi-gpio, vcgencmd, df, cat, command -v)
        # plus integers validated by policy.check_pin -- there is no tool that
        # runs caller-supplied commands on the Pi. ssh is resolved via PATH.
        # SSH_BASE pins host-key checking and disables agent/X11/ProxyCommand.
        return subprocess.run(  # noqa: S603
            full,
            capture_output=True,
            text=True,
            timeout=config.pi_ssh_timeout + 5,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError(
            errors.SSH_FAILED, f"SSH to {host} timed out.", "Check the Pi is powered and reachable."
        ) from exc
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


def probe_tools(host: str, config: Config) -> dict[str, bool]:
    if host not in _TOOLS:
        found = {}
        for name in TOOL_NAMES:
            result = _run(host, ["command", "-v", name], config)
            found[name] = result.returncode == 0 and bool(result.stdout.strip())
        _TOOLS[host] = found
        if found["pinctrl"]:
            _BACKENDS[host] = "pinctrl"
        elif found["raspi-gpio"]:
            _BACKENDS[host] = "raspi-gpio"
    return _TOOLS[host]


def detect_backend(host: str, config: Config) -> str:
    if host in config.jetson_hosts:
        raise ToolError(
            errors.HOST_NOT_ALLOWED,
            "GPIO tools are for Raspberry Pi hosts, not Jetson.",
            "Use jetson_inventory on hosts listed under [jetson] hosts.",
        )
    if host in _BACKENDS:
        return _BACKENDS[host]
    probe_tools(host, config)
    if host in _BACKENDS:
        return _BACKENDS[host]
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
            flags = [t for t in (fields.get("flags") or "").split() if t != "--"]
            pins.append(
                {
                    "bcm": int(fields["bcm"]),
                    "mode": fields["mode"],
                    "pull": next((t for t in flags if t in PULLS), None),
                    "drive": next((t for t in flags if t in DRIVES), None),
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
                    "drive": None,
                    "level": int(fields["level"]),
                    "name": None,
                }
            )
    return pins


def list_pins(host: str, config: Config) -> list[dict[str, Any]]:
    backend = detect_backend(host, config)
    result = _run(host, [backend, "get"], config)
    if result.returncode != 0:
        raise _fail(host, result)
    return _parse_pins(backend, result.stdout)


def read_pin(host: str, bcm: int, config: Config) -> dict[str, Any]:
    backend = detect_backend(host, config)
    result = _run(host, [backend, "get", str(bcm)], config)
    if result.returncode != 0:
        raise _fail(host, result)

    pins = [pin for pin in _parse_pins(backend, result.stdout) if pin["bcm"] == bcm]
    if len(pins) != 1:
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


def _read_text(host: str, path: str, config: Config) -> str | None:
    """Read one fixed remote file; optional diagnostics do not fail the report."""
    result = _run(host, ["cat", path], config)
    if result.returncode != 0:
        return None
    return result.stdout[:16_384]


def _os_release(text: str | None) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (text or "").splitlines():
        key, separator, value = line.partition("=")
        if separator and key.isidentifier():
            values[key] = value.strip().strip('"')
    return values


def pi_generation(model: str | None) -> str:
    text = (model or "").replace("\x00", " ")
    checks = (
        ("Raspberry Pi 5", "pi5"),
        ("Raspberry Pi 4", "pi4"),
        ("Raspberry Pi 3", "pi3"),
        ("Raspberry Pi 2", "pi2"),
        ("Raspberry Pi Zero 2", "pi_zero2"),
        ("Raspberry Pi Zero", "pi_zero"),
    )
    for needle, generation in checks:
        if needle in text:
            return generation
    return "unknown"


def parse_throttled(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    line = text.strip().splitlines()[0] if text.strip() else ""
    _, _, hex_part = line.partition("=")
    raw = (hex_part or line).strip()
    try:
        flags = int(raw, 16)
    except ValueError:
        return None
    decoded = {name: bool(flags & bit) for bit, name in THROTTLED_BITS}
    decoded["raw"] = f"0x{flags:x}"
    return decoded


def parse_df_root(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    parts = lines[-1].split()
    if len(parts) < 6:
        return None
    try:
        used = int(parts[2])
        available = int(parts[3])
        capacity = int(parts[4].rstrip("%"))
    except ValueError:
        return None
    return {
        "filesystem": parts[0][:128],
        "used_kib": used,
        "available_kib": available,
        "capacity_percent": capacity,
        "mounted_on": parts[5][:64],
    }


def inventory(host: str, config: Config) -> dict[str, Any]:
    """Collect bounded, read-only Pi diagnostics using fixed file reads."""
    if host in config.jetson_hosts:
        raise ToolError(
            errors.HOST_NOT_ALLOWED,
            "GPIO and Pi inventory tools are for Raspberry Pi hosts, not Jetson.",
            "Use jetson_inventory on hosts listed under [jetson] hosts.",
        )
    model_text = _read_text(host, "/proc/device-tree/model", config)
    model = model_text.strip().strip("\x00") if model_text else None
    release = _os_release(_read_text(host, "/etc/os-release", config))
    kernel = _read_text(host, "/proc/sys/kernel/osrelease", config)
    temperature = _read_text(host, "/sys/class/thermal/thermal_zone0/temp", config)
    load = _read_text(host, "/proc/loadavg", config)
    tools = probe_tools(host, config)
    backend = detect_backend(host, config)
    generation = pi_generation(model)

    temperature_c = None
    if temperature and temperature.strip().isdigit():
        temperature_c = round(int(temperature.strip()) / 1000, 1)

    load_average = None
    if load:
        first = load.split(maxsplit=1)[0]
        try:
            load_average = float(first)
        except ValueError:
            pass

    throttled = None
    if tools.get("vcgencmd"):
        result = _run(host, ["vcgencmd", "get_throttled"], config)
        if result.returncode == 0:
            throttled = parse_throttled(result.stdout[:256])

    storage = None
    df = _run(host, ["df", "-P", "/"], config)
    if df.returncode == 0:
        storage = parse_df_root(df.stdout[:2048])

    network = []
    for iface in NET_IFACES:
        state = _read_text(host, f"/sys/class/net/{iface}/operstate", config)
        if state:
            network.append({"name": iface, "operstate": state.strip().splitlines()[0][:16]})

    warnings: list[str] = []
    if generation == "pi5" and backend == "raspi-gpio":
        warnings.append("Pi 5 is using raspi-gpio; pin functions differ from Pi 3/4. Prefer pinctrl.")

    device: CapabilityDevice = {
        "family": "raspberry_pi",
        "model": model,
        "identity": host,
        "capabilities": support.capability_names("raspberry_pi"),
        "health": "reachable",
        "operations": support.operations_for("raspberry_pi"),
    }
    return {
        "device": device,
        "os": {
            "id": release.get("ID"),
            "name": release.get("PRETTY_NAME") or release.get("NAME"),
            "version": release.get("VERSION_ID"),
        },
        "kernel": kernel.strip() if kernel else None,
        "generation": generation,
        "gpio_backend": backend,
        "tools": tools,
        "temperature_c": temperature_c,
        "load_average_1m": load_average,
        "throttled": throttled,
        "storage": storage,
        "network": network,
        "warnings": warnings,
    }
