"""The user's parts inventory: what is on the bench, so project ideas use it.

~/.config/omarchy-hardware/parts.toml is written by the user and only read here.
It is not secret, so unlike config.toml it may be group-readable, but it must be
a regular file owned by the user. Every field is bounded, because the whole list
is returned to the model.

    [[part]]
    name = "BME280"
    kind = "sensor"
    qty = 2
    interface = "i2c"
    i2c_address = "0x76"
    voltage = "3.3-5"
    notes = "breakout with pull-ups"
"""

from __future__ import annotations

import errno
import os
import re
import stat
import tomllib
from pathlib import Path
from typing import Any

from . import errors
from .config import CONFIG_DIR
from .errors import ToolError

FILE_NAME = "parts.toml"
MAX_PARTS = 500
MAX_FILE_BYTES = 256 * 1024
KINDS = ("sensor", "display", "actuator", "motor_driver", "module", "input", "power", "passive", "board", "other")
INTERFACES = ("i2c", "spi", "uart", "onewire", "analog", "digital", "pwm", "usb", "other")
_TEXT_LIMITS = {"name": 60, "voltage": 20, "notes": 200}
_KEYS = {"name", "kind", "qty", "interface", "i2c_address", "voltage", "notes"}
_I2C_ADDRESS = re.compile(r"0x[0-7][0-9a-fA-F]")
_PRINTABLE = re.compile(r"[^\x00-\x1f\x7f]*")


def parts_path() -> Path:
    return CONFIG_DIR / FILE_NAME


def _invalid(message: str) -> ToolError:
    return ToolError(errors.CONFIG_ERROR, f"{FILE_NAME}: {message}", f"Fix {parts_path()} and call again.")


def _read_raw() -> dict[str, Any] | None:
    path = parts_path()
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        if exc.errno == errno.ENOENT:
            return None
        if exc.errno == errno.ELOOP:
            raise _invalid("must be a regular file, not a symlink") from exc
        raise _invalid(f"cannot be read ({exc.strerror})") from exc
    with os.fdopen(fd, "rb") as handle:
        st = os.fstat(handle.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
            raise _invalid("must be a regular file owned by the current user")
        data = handle.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise _invalid(f"is larger than {MAX_FILE_BYTES // 1024} KiB")
    try:
        return tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise _invalid(f"is not valid TOML ({exc})") from exc


def _text(entry: dict[str, Any], key: str, where: str, required: bool = False) -> str | None:
    value = entry.get(key)
    if value is None:
        if required:
            raise _invalid(f"{where} needs {key}")
        return None
    if not isinstance(value, str) or not value.strip():
        raise _invalid(f"{where}.{key} must be a non-empty string")
    if len(value) > _TEXT_LIMITS[key] or not _PRINTABLE.fullmatch(value):
        raise _invalid(f"{where}.{key} must be at most {_TEXT_LIMITS[key]} printable characters on one line")
    return value.strip()


def _part(entry: object, index: int) -> dict[str, Any]:
    where = f"part[{index}]"
    if not isinstance(entry, dict):
        raise _invalid(f"{where} must be a table")
    unknown = set(entry) - _KEYS
    if unknown:
        raise _invalid(f"{where} has unknown keys {sorted(unknown)}")

    part: dict[str, Any] = {"name": _text(entry, "name", where, required=True)}
    kind = entry.get("kind", "other")
    if kind not in KINDS:
        raise _invalid(f"{where}.kind must be one of {', '.join(KINDS)}")
    part["kind"] = kind

    qty = entry.get("qty", 1)
    if not isinstance(qty, int) or isinstance(qty, bool) or not 0 <= qty <= 10_000:
        raise _invalid(f"{where}.qty must be an integer from 0 to 10000")
    part["qty"] = qty

    interface = entry.get("interface")
    if interface is not None and interface not in INTERFACES:
        raise _invalid(f"{where}.interface must be one of {', '.join(INTERFACES)}")
    part["interface"] = interface

    address = entry.get("i2c_address")
    if address is not None:
        if not isinstance(address, str) or not _I2C_ADDRESS.fullmatch(address):
            raise _invalid(f"{where}.i2c_address must be a 7-bit address written like 0x76")
        part["i2c_address"] = address.lower()

    for key in ("voltage", "notes"):
        value = _text(entry, key, where)
        if value is not None:
            part[key] = value
    return part


def load() -> dict[str, Any]:
    raw = _read_raw()
    if raw is None:
        return {"configured": False, "parts": []}
    unknown = set(raw) - {"part"}
    if unknown:
        raise _invalid(f"unknown top-level keys {sorted(unknown)}; list parts as [[part]] tables")
    entries = raw.get("part", [])
    if not isinstance(entries, list):
        raise _invalid("parts must be [[part]] tables")
    if len(entries) > MAX_PARTS:
        raise _invalid(f"lists more than {MAX_PARTS} parts")
    items = [_part(entry, index) for index, entry in enumerate(entries)]
    return {"configured": True, "parts": items}
