"""Name I2C devices from their address and chip-ID register.

The data is peripherals.toml, shipped with the package. identify() takes what
the bench probe reported (addresses plus the ID registers it read) and returns
candidates. A matching chip ID is "confirmed"; an address alone is "possible".

The probe's report is device output, relayed by the model. It is parsed as data
with strict shapes and bounds, and only compared against the shipped table.
"""

from __future__ import annotations

import functools
import re
import tomllib
from pathlib import Path
from typing import Any

from . import errors
from .errors import ToolError

DATA = Path(__file__).resolve().parent / "peripherals.toml"
HEX_BYTE = re.compile(r"0x[0-9a-f]{2}")
MAX_DEVICES = 128
MAX_REGISTERS = 16
_KEYS = {"name", "kind", "measures", "addresses", "id_register", "id_values", "notes"}


def _hex(value: object) -> str | None:
    if isinstance(value, str) and HEX_BYTE.fullmatch(value.strip().lower()):
        return value.strip().lower()
    return None


@functools.cache
def table() -> tuple[dict[str, Any], ...]:
    with DATA.open("rb") as handle:
        raw = tomllib.load(handle)
    devices = []
    for index, entry in enumerate(raw.get("device", [])):
        where = f"peripherals.toml device[{index}]"
        if set(entry) - _KEYS or not {"name", "kind", "measures", "addresses"} <= set(entry):
            raise ValueError(f"{where}: unexpected or missing keys")
        addresses = [_hex(address) for address in entry["addresses"]]
        if not addresses or None in addresses or any(int(a, 16) > 0x77 for a in addresses):
            raise ValueError(f"{where}: addresses must be 7-bit hex bytes like 0x76")
        has_id = "id_register" in entry
        if has_id != ("id_values" in entry):
            raise ValueError(f"{where}: id_register and id_values go together")
        if has_id and (_hex(entry["id_register"]) is None or not entry["id_values"]
                       or any(_hex(v) is None for v in entry["id_values"])):
            raise ValueError(f"{where}: id_register and id_values must be hex bytes")
        devices.append(entry)
    return tuple(devices)


def id_reads() -> dict[str, set[str]]:
    """Address -> ID registers worth reading there. The probe sketch mirrors this."""
    reads: dict[str, set[str]] = {}
    for device in table():
        if "id_register" in device:
            for address in device["addresses"]:
                reads.setdefault(address, set()).add(device["id_register"])
    return reads


def candidates(address: str) -> list[dict[str, Any]]:
    return [device for device in table() if address in device["addresses"]]


def _parse_report(devices: object) -> list[tuple[str, dict[str, str]]]:
    if not isinstance(devices, list) or len(devices) > MAX_DEVICES:
        raise ToolError(errors.INVALID_ARGUMENT, f"devices must be a list of at most {MAX_DEVICES} entries.")
    parsed = []
    for item in devices:
        if isinstance(item, str):
            item = {"address": item}
        if not isinstance(item, dict):
            raise ToolError(errors.INVALID_ARGUMENT, "Each device is an address string or {address, id}.")
        address = _hex(item.get("address", item.get("a")))
        if address is None or int(address, 16) > 0x77:
            raise ToolError(errors.INVALID_ARGUMENT, f"{item.get('address')!r} is not a 7-bit I2C address like 0x76.")
        registers = item.get("id", {}) or {}
        if not isinstance(registers, dict) or len(registers) > MAX_REGISTERS:
            raise ToolError(errors.INVALID_ARGUMENT, f"id for {address} must map at most {MAX_REGISTERS} registers.")
        reads = {}
        for register, value in registers.items():
            register_hex, value_hex = _hex(register), _hex(value)
            if register_hex and value_hex:
                reads[register_hex] = value_hex
        parsed.append((address, reads))
    return parsed


def identify(devices: object, inventory: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    owned = {}
    for part in inventory or []:
        if part.get("i2c_address"):
            owned.setdefault(part["i2c_address"], []).append(part["name"])

    results = []
    for address, reads in _parse_report(devices):
        confirmed, possible, ruled_out = [], [], []
        for device in candidates(address):
            summary = {"name": device["name"], "kind": device["kind"], "measures": device["measures"]}
            if device.get("notes"):
                summary["notes"] = device["notes"]
            register = device.get("id_register")
            if register and register in reads:
                if reads[register] in device["id_values"]:
                    confirmed.append({**summary, "evidence": f"register {register} reads {reads[register]}"})
                else:
                    ruled_out.append(device["name"])
            else:
                possible.append(summary)
        entry: dict[str, Any] = {
            "address": address,
            "confirmed": confirmed,
            # Once a chip ID confirms a part, address-only guesses are noise.
            "possible": [] if confirmed else possible,
            "ruled_out": ruled_out,
        }
        if address in owned:
            entry["in_parts_inventory"] = owned[address]
        if not confirmed and not possible:
            entry["note"] = "Not in the table. Look up the address with the part names the user has."
        results.append(entry)
    return results
