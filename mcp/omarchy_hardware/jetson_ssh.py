"""Read-only NVIDIA Jetson inventory over SSH.

Jetson is a separate family from Raspberry Pi. There is no GPIO tool here and no
arbitrary remote command: only fixed `cat`/`df`/`nvpmodel` argv lists.
"""

from __future__ import annotations

import re
from typing import Any

from . import errors, support
from .capabilities import CapabilityDevice
from .config import Config
from .errors import ToolError
from .gpio_ssh import _fail, _os_release, _run, parse_df_root

JETSON_SECTION = "[jetson] hosts"
NET_IFACES = ("eth0", "wlan0", "end0", "usb0")
NVPMODEL = "nvpmodel"
L4T_LINE = re.compile(r"R(\d+)\s*\(release\).*?REVISION:\s*([0-9.]+)", re.IGNORECASE)


def _ssh(host: str, argv: list[str], config: Config):
    return _run(host, argv, config, hosts=config.jetson_hosts, section=JETSON_SECTION)


def _read(host: str, path: str, config: Config) -> str | None:
    result = _ssh(host, ["cat", path], config)
    if result.returncode != 0:
        return None
    return result.stdout[:16_384]


def parse_l4t_release(text: str | None) -> str | None:
    if not text:
        return None
    match = L4T_LINE.search(text)
    return f"{match.group(1)}.{match.group(2)}" if match else None


def jetson_generation(model: str | None) -> str:
    text = (model or "").replace("\x00", " ")
    checks = (
        ("Orin", "orin"),
        ("Xavier", "xavier"),
        ("TX2", "tx2"),
        ("TX1", "tx1"),
        ("Nano", "nano"),
    )
    for needle, generation in checks:
        if needle in text:
            return generation
    if "Jetson" in text or "tegra" in text.lower():
        return "jetson"
    return "unknown"


def status(host: str, config: Config) -> dict[str, Any]:
    import time

    started = time.monotonic()
    result = _ssh(host, ["cat", "/proc/device-tree/model"], config)
    latency_ms = round((time.monotonic() - started) * 1000)
    if result.returncode != 0:
        raise _fail(host, result)
    model = result.stdout.strip().strip("\x00") or None
    return {
        "host": host,
        "reachable": True,
        "model": model,
        "generation": jetson_generation(model),
        "latency_ms": latency_ms,
    }


def inventory(host: str, config: Config) -> dict[str, Any]:
    model_text = _read(host, "/proc/device-tree/model", config)
    model = model_text.strip().strip("\x00") if model_text else None
    generation = jetson_generation(model)
    if generation == "unknown" and model and "raspberry pi" in model.lower():
        raise ToolError(
            errors.HOST_NOT_ALLOWED,
            "This host looks like a Raspberry Pi; Jetson tools will not run there.",
            "List Raspberry Pi hosts under [pi] hosts.",
        )

    release = _os_release(_read(host, "/etc/os-release", config))
    kernel = _read(host, "/proc/sys/kernel/osrelease", config)
    l4t = parse_l4t_release(_read(host, "/etc/nv_tegra_release", config))
    temperature = _read(host, "/sys/devices/virtual/thermal/thermal_zone0/temp", config)
    load = _read(host, "/proc/loadavg", config)

    temperature_c = None
    if temperature and temperature.strip().lstrip("-").isdigit():
        temperature_c = round(int(temperature.strip()) / 1000, 1)

    load_average = None
    if load:
        first = load.split(maxsplit=1)[0]
        try:
            load_average = float(first)
        except ValueError:
            pass

    nvpmodel = None
    probe = _ssh(host, ["command", "-v", NVPMODEL], config)
    if probe.returncode == 0 and probe.stdout.strip():
        queried = _ssh(host, [NVPMODEL, "-q"], config)
        if queried.returncode == 0:
            nvpmodel = queried.stdout.strip().splitlines()[0][:128] if queried.stdout.strip() else None

    storage = None
    df = _ssh(host, ["df", "-P", "/"], config)
    if df.returncode == 0:
        storage = parse_df_root(df.stdout[:2048])

    network = []
    for iface in NET_IFACES:
        state = _read(host, f"/sys/class/net/{iface}/operstate", config)
        if state:
            network.append({"name": iface, "operstate": state.strip().splitlines()[0][:16]})

    device: CapabilityDevice = {
        "family": "jetson",
        "model": model,
        "identity": host,
        "capabilities": support.capability_names("jetson"),
        "health": "reachable",
        "operations": support.operations_for("jetson"),
    }
    return {
        "device": device,
        "os": {
            "id": release.get("ID"),
            "name": release.get("PRETTY_NAME") or release.get("NAME"),
            "version": release.get("VERSION_ID"),
        },
        "kernel": kernel.strip() if kernel else None,
        "l4t": l4t,
        "generation": generation,
        "nvpmodel": nvpmodel,
        "temperature_c": temperature_c,
        "load_average_1m": load_average,
        "storage": storage,
        "network": network,
    }
