"""Strict, packaged catalog of display controllers and their wiring needs."""

from __future__ import annotations

import copy
import functools
import re
import tomllib
from pathlib import Path
from typing import Any

from . import errors
from .errors import ToolError

DATA = Path(__file__).resolve().parent / "displays.toml"
_KEYS = {
    "controller", "kind", "interfaces", "logic_voltage", "resolutions", "color",
    "i2c_addresses", "roles", "libraries", "notes", "sources",
}
_REQUIRED = _KEYS - {"i2c_addresses"}
_KINDS = {"tft", "oled", "character_lcd"}
_INTERFACES = {"spi", "i2c", "parallel8"}
_COLORS = {"rgb565", "mono", "character"}
_RESOLUTION = re.compile(r"[1-9][0-9]{0,3}x[1-9][0-9]{0,2}")
_ADDRESS = re.compile(r"0x[0-7][0-9a-f]{1}")
_LIBRARY_KEYS = {"name", "classes"}


def _strings(value: object, where: str) -> list[str]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{where} must be a non-empty list of strings")
    return value


def _valid_entry(entry: object, index: int) -> dict[str, Any]:
    where = f"displays.toml display[{index}]"
    if not isinstance(entry, dict) or set(entry) - _KEYS or not _REQUIRED <= set(entry):
        raise ValueError(f"{where}: unexpected or missing keys")
    if not isinstance(entry["controller"], str) or not entry["controller"].strip():
        raise ValueError(f"{where}: controller must be a non-empty string")
    if not isinstance(entry["kind"], str) or entry["kind"] not in _KINDS:
        raise ValueError(f"{where}: kind must be one of {sorted(_KINDS)}")
    interfaces = _strings(entry["interfaces"], f"{where}.interfaces")
    if len(set(interfaces)) != len(interfaces) or not set(interfaces) <= _INTERFACES:
        raise ValueError(f"{where}: interfaces must be unique values from {sorted(_INTERFACES)}")
    voltage = entry["logic_voltage"]
    if isinstance(voltage, bool) or not isinstance(voltage, int | float) or not 0 < voltage <= 5.5:
        raise ValueError(f"{where}: logic_voltage must be a number above 0 and at most 5.5")
    resolutions = _strings(entry["resolutions"], f"{where}.resolutions")
    if any(not _RESOLUTION.fullmatch(resolution) for resolution in resolutions):
        raise ValueError(f"{where}: resolutions must use WIDTHxHEIGHT notation")
    if not isinstance(entry["color"], str) or entry["color"] not in _COLORS:
        raise ValueError(f"{where}: color must be one of {sorted(_COLORS)}")
    if entry["kind"] == "character_lcd" and entry["color"] != "character":
        raise ValueError(f"{where}: character_lcd entries must use character color")
    if entry["kind"] != "character_lcd" and entry["color"] == "character":
        raise ValueError(f"{where}: character color is only for character_lcd entries")

    addresses = entry.get("i2c_addresses")
    if ("i2c" in interfaces) != (addresses is not None):
        raise ValueError(f"{where}: i2c_addresses are required only for I2C interfaces")
    if addresses is not None:
        addresses = _strings(addresses, f"{where}.i2c_addresses")
        if len(set(addresses)) != len(addresses) or any(
            not _ADDRESS.fullmatch(address) for address in addresses
        ):
            raise ValueError(f"{where}: i2c_addresses must be unique 7-bit hex addresses")

    roles = entry["roles"]
    if not isinstance(roles, dict) or set(roles) != set(interfaces):
        raise ValueError(f"{where}: roles must define each interface exactly once")
    from . import wiring

    for interface, role_list in roles.items():
        role_list = _strings(role_list, f"{where}.roles.{interface}")
        if len(set(role_list)) != len(role_list) or not set(role_list) <= wiring.ROLES.keys():
            raise ValueError(f"{where}: roles.{interface} must contain unique wiring roles")

    libraries = entry["libraries"]
    if not isinstance(libraries, list) or not libraries:
        raise ValueError(f"{where}.libraries must be a non-empty list")
    for library in libraries:
        if not isinstance(library, dict) or set(library) != _LIBRARY_KEYS:
            raise ValueError(f"{where}.libraries entries require name and classes")
        if not isinstance(library["name"], str) or not library["name"].strip():
            raise ValueError(f"{where}.libraries.name must be a non-empty string")
        _strings(library["classes"], f"{where}.libraries.classes")
    _strings(entry["notes"], f"{where}.notes")
    sources = _strings(entry["sources"], f"{where}.sources")
    if any(not source.startswith(("https://", "http://")) for source in sources):
        raise ValueError(f"{where}.sources entries must be URLs")
    return entry


@functools.cache
def table() -> tuple[dict[str, Any], ...]:
    with DATA.open("rb") as handle:
        raw = tomllib.load(handle)
    if set(raw) != {"display"} or not isinstance(raw["display"], list) or not raw["display"]:
        raise ValueError("displays.toml must contain a non-empty display array")
    entries = [_valid_entry(entry, index) for index, entry in enumerate(raw["display"])]
    names = [entry["controller"].casefold() for entry in entries]
    if len(names) != len(set(names)):
        raise ValueError("displays.toml contains duplicate controller names")
    return tuple(entries)


def catalog() -> list[dict[str, Any]]:
    """Return all catalog entries sorted by controller name."""
    return copy.deepcopy(sorted(table(), key=lambda entry: entry["controller"].casefold()))


def lookup(controller: str) -> dict[str, Any]:
    """Find one controller by name, ignoring case."""
    name = controller.strip().casefold() if isinstance(controller, str) else ""
    for entry in table():
        if entry["controller"].casefold() == name:
            return copy.deepcopy(entry)
    known = ", ".join(sorted(entry["controller"] for entry in table()))
    raise ToolError(
        errors.INVALID_ARGUMENT,
        f"Unknown display controller {controller!r}.",
        f"Known controllers: {known}.",
    )


def export() -> dict[str, Any]:
    """Return catalog data with a clear statement about its limits."""
    return {
        "displays": catalog(),
        "data_status": (
            "Based on controller datasheets and library sources, not physically validated; "
            "breakout boards differ."
        ),
    }
