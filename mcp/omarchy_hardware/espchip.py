"""Validated ESP chip identity from esptool v5, shared by probes and backups."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from . import errors, flash
from .errors import ToolError
from .ids import ESP_CHIP_FQBNS

BAUD = "460800"
MAC = re.compile(r"^MAC:\s*([0-9a-f]{2}(?::[0-9a-f]{2}){5}(?:(?::[0-9a-f]{2}){2})?)\s*$", re.I | re.M)
CHIP = re.compile(r"^(?:Chip is|Chip type:)\s*([^\r\n]+)", re.M)
DETAIL = re.compile(r"[A-Za-z0-9 ()._/-]{1,120}")
VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def esptool_path() -> str:
    configured = os.environ.get("OMARCHY_HARDWARE_ESPTOOL")
    data = os.environ.get("OMARCHY_HARDWARE_ARDUINO_DATA")
    if data and not os.path.isabs(data):
        raise ToolError(errors.CONFIG_ERROR, "OMARCHY_HARDWARE_ARDUINO_DATA must be an absolute path.")
    if configured:
        if not os.path.isabs(configured):
            raise ToolError(errors.CONFIG_ERROR, "OMARCHY_HARDWARE_ESPTOOL must be an absolute path.")
        candidates = [Path(configured)]
    else:
        root = Path(data or Path.home() / ".arduino15")
        versions = []
        for path in root.glob("packages/esp32/tools/esptool_py/*/esptool"):
            match = VERSION.fullmatch(path.parent.name)
            if match and int(match[1]) >= 5:
                versions.append((tuple(map(int, match.groups())), path))
        candidates = [path for _, path in sorted(versions, reverse=True)]
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    raise ToolError(errors.TOOL_MISSING, "esptool v5 or newer was not found.",
                    "Install the ESP32 core (it ships esptool), or set OMARCHY_HARDWARE_ESPTOOL to esptool v5+.")


def _esptool(port: str, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    try:
        return flash.run_group([esptool_path(), "--port", port, "--baud", BAUD, *args], timeout)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise ToolError(errors.SERIAL_ERROR, "esptool failed or timed out.",
                        "Hold BOOT while it connects, then retry.") from exc


def _failed(result: subprocess.CompletedProcess, what: str) -> ToolError:
    # Do not echo device-controlled stderr as instructions in an error hint.
    return ToolError(errors.SERIAL_ERROR, f"esptool could not {what}.",
                     "Hold BOOT while it connects, then retry.")


def _identity(result: subprocess.CompletedProcess) -> dict[str, str]:
    text = result.stdout or ""
    if result.returncode != 0:
        raise _failed(result, "identify the chip")
    chip = CHIP.search(text)
    detail = chip[1].strip() if chip else ""
    if not DETAIL.fullmatch(detail):
        raise ToolError(errors.UNSUPPORTED_OPERATION, "esptool did not report a supported ESP32 chip.")
    name = detail.split(" ", 1)[0]
    family = name if name in ESP_CHIP_FQBNS else None
    if re.fullmatch(r"ESP32-(?:D0WD[A-Z0-9-]*|D2WD|S0WD|U4WDH|PICO-[A-Z0-9-]+)", name):
        family = "ESP32"
    if family is None:
        raise ToolError(errors.UNSUPPORTED_OPERATION, "esptool reported an unknown chip family.")
    mac = MAC.search(text)
    if mac is None:
        raise _failed(result, "read the chip's MAC address")
    return {"chip": family, "chip_detail": detail, "mac": mac[1].lower()}


def identify(port: str) -> dict[str, str]:
    """Quick live chip family and factory MAC check, resetting running firmware."""
    return _identity(_esptool(port, "read-mac"))


def probe(port: str) -> dict[str, Any]:
    result = _esptool(port, "flash-id")
    info = _identity(result)
    text = result.stdout or ""

    def size(pattern: str) -> int | None:
        match = re.search(pattern, text)
        return int(match[1]) if match else None

    mode = re.search(r"^USB mode:\s*(USB-Serial/JTAG|USB-OTG)\s*$", text, re.M)
    return {**info, "flash_mb": size(r"Detected flash size:\s*(\d{1,4})MB\b"),
            "psram_mb": size(r"Embedded PSRAM (\d{1,4})MB\b"), "usb_mode": mode[1] if mode else None}
