"""Honest hardware support matrix.

Availability is independent of safety:

- supported: implemented and intended for use
- experimental: implemented, but not physically validated
- unsupported: not implemented; tools must not pretend otherwise
"""

from __future__ import annotations

from typing import TypedDict

from .capabilities import CapabilityOperation
from .errors import UNSUPPORTED_OPERATION, ToolError

AVAIL_SUPPORTED = "supported"
AVAIL_EXPERIMENTAL = "experimental"
AVAIL_UNSUPPORTED = "unsupported"

SAFETY_READ_ONLY = "read_only"
SAFETY_STATE_CHANGING = "state_changing"
SAFETY_DESTRUCTIVE = "destructive"

FAMILIES = (
    "raspberry_pi",
    "jetson",
    "microcontroller",
    "siemens_logo",
    "siemens_s7",
    "omron",
    "schneider",
    "weintek_hmi",
)


class SupportRow(TypedDict):
    id: str
    availability: str
    safety: str
    requires_confirmation: bool


def _row(op_id: str, availability: str, safety: str, confirm: bool = False) -> SupportRow:
    return {
        "id": op_id,
        "availability": availability,
        "safety": safety,
        "requires_confirmation": confirm,
    }


def _plc_unsupported() -> tuple[SupportRow, ...]:
    return (
        _row("plc.discover", AVAIL_UNSUPPORTED, SAFETY_READ_ONLY),
        _row("plc.read", AVAIL_UNSUPPORTED, SAFETY_READ_ONLY),
        _row("plc.write", AVAIL_UNSUPPORTED, SAFETY_DESTRUCTIVE, True),
    )


# GPIO writes exist in the MCP server but have no physical validation row.
# Jetson and industrial MCP tools are not shipped; C# adapters do not change that.
MATRIX: dict[str, tuple[SupportRow, ...]] = {
    "raspberry_pi": (
        _row("pi.inventory", AVAIL_EXPERIMENTAL, SAFETY_READ_ONLY),
        _row("pi.status", AVAIL_EXPERIMENTAL, SAFETY_READ_ONLY),
        _row("gpio.list", AVAIL_EXPERIMENTAL, SAFETY_READ_ONLY),
        _row("gpio.read", AVAIL_EXPERIMENTAL, SAFETY_READ_ONLY),
        _row("gpio.set_mode", AVAIL_EXPERIMENTAL, SAFETY_STATE_CHANGING, True),
        _row("gpio.write", AVAIL_EXPERIMENTAL, SAFETY_DESTRUCTIVE, True),
        _row("gpio.pwm", AVAIL_UNSUPPORTED, SAFETY_STATE_CHANGING, True),
        _row("gpio.spi", AVAIL_UNSUPPORTED, SAFETY_STATE_CHANGING, True),
        _row("gpio.i2c", AVAIL_UNSUPPORTED, SAFETY_STATE_CHANGING, True),
    ),
    "jetson": (
        _row("jetson.inventory", AVAIL_UNSUPPORTED, SAFETY_READ_ONLY),
        _row("jetson.status", AVAIL_UNSUPPORTED, SAFETY_READ_ONLY),
        _row("jetson.telemetry", AVAIL_UNSUPPORTED, SAFETY_READ_ONLY),
    ),
    "microcontroller": (
        _row("board.list", AVAIL_EXPERIMENTAL, SAFETY_READ_ONLY),
        _row("serial.open", AVAIL_EXPERIMENTAL, SAFETY_STATE_CHANGING),
        _row("serial.read", AVAIL_EXPERIMENTAL, SAFETY_READ_ONLY),
        _row("serial.write", AVAIL_EXPERIMENTAL, SAFETY_DESTRUCTIVE, True),
        _row("flash.compile", AVAIL_EXPERIMENTAL, SAFETY_READ_ONLY),
        _row("flash.upload", AVAIL_EXPERIMENTAL, SAFETY_DESTRUCTIVE, True),
    ),
    "siemens_logo": _plc_unsupported(),
    "siemens_s7": _plc_unsupported(),
    "omron": _plc_unsupported(),
    "schneider": _plc_unsupported(),
    "weintek_hmi": (
        _row("hmi.identify", AVAIL_UNSUPPORTED, SAFETY_READ_ONLY),
        _row("opcua.read", AVAIL_UNSUPPORTED, SAFETY_READ_ONLY),
        _row("opcua.write", AVAIL_UNSUPPORTED, SAFETY_DESTRUCTIVE, True),
        _row("mqtt.subscribe", AVAIL_UNSUPPORTED, SAFETY_STATE_CHANGING),
        _row("mqtt.publish", AVAIL_UNSUPPORTED, SAFETY_DESTRUCTIVE, True),
    ),
}


def lookup(family: str, operation: str) -> SupportRow:
    rows = MATRIX.get(family)
    if rows:
        for row in rows:
            if row["id"] == operation:
                return row
    return {
        "id": operation,
        "availability": AVAIL_UNSUPPORTED,
        "safety": SAFETY_READ_ONLY,
        "requires_confirmation": False,
    }


def operations_for(family: str) -> list[CapabilityOperation]:
    return [
        {
            "id": row["id"],
            "safety": row["safety"],
            "available": row["availability"] != AVAIL_UNSUPPORTED,
        }
        for row in MATRIX.get(family, ())
    ]


def capability_names(family: str) -> list[str]:
    return [row["id"] for row in MATRIX.get(family, ()) if row["availability"] != AVAIL_UNSUPPORTED]


def export(family: str | None = None) -> dict[str, list[SupportRow]]:
    if family is None:
        return {name: list(rows) for name, rows in MATRIX.items()}
    if family not in MATRIX:
        raise ToolError(
            UNSUPPORTED_OPERATION,
            f"Unknown hardware family {family!r}.",
            f"Use one of: {', '.join(FAMILIES)}.",
        )
    return {family: list(MATRIX[family])}


def unsupported(family: str, operation: str) -> ToolError:
    row = lookup(family, operation)
    return ToolError(
        UNSUPPORTED_OPERATION,
        f"{operation} is {row['availability']} for {family}.",
        f"See docs/support-matrix.md ({family} / {operation}).",
    )
