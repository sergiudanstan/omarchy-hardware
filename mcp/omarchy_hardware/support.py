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


# GPIO writes exist in the MCP server but have no physical validation row.
# Jetson/Siemens MCP tools are not shipped; C# adapters do not change that.
MATRIX: dict[str, tuple[SupportRow, ...]] = {
    "raspberry_pi": (
        {
            "id": "pi.inventory",
            "availability": AVAIL_EXPERIMENTAL,
            "safety": SAFETY_READ_ONLY,
            "requires_confirmation": False,
        },
        {
            "id": "pi.status",
            "availability": AVAIL_EXPERIMENTAL,
            "safety": SAFETY_READ_ONLY,
            "requires_confirmation": False,
        },
        {
            "id": "gpio.list",
            "availability": AVAIL_EXPERIMENTAL,
            "safety": SAFETY_READ_ONLY,
            "requires_confirmation": False,
        },
        {
            "id": "gpio.read",
            "availability": AVAIL_EXPERIMENTAL,
            "safety": SAFETY_READ_ONLY,
            "requires_confirmation": False,
        },
        {
            "id": "gpio.set_mode",
            "availability": AVAIL_EXPERIMENTAL,
            "safety": SAFETY_STATE_CHANGING,
            "requires_confirmation": True,
        },
        {
            "id": "gpio.write",
            "availability": AVAIL_EXPERIMENTAL,
            "safety": SAFETY_DESTRUCTIVE,
            "requires_confirmation": True,
        },
        {
            "id": "gpio.pwm",
            "availability": AVAIL_UNSUPPORTED,
            "safety": SAFETY_STATE_CHANGING,
            "requires_confirmation": True,
        },
        {
            "id": "gpio.spi",
            "availability": AVAIL_UNSUPPORTED,
            "safety": SAFETY_STATE_CHANGING,
            "requires_confirmation": True,
        },
        {
            "id": "gpio.i2c",
            "availability": AVAIL_UNSUPPORTED,
            "safety": SAFETY_STATE_CHANGING,
            "requires_confirmation": True,
        },
    ),
    "jetson": (
        {
            "id": "jetson.inventory",
            "availability": AVAIL_UNSUPPORTED,
            "safety": SAFETY_READ_ONLY,
            "requires_confirmation": False,
        },
        {
            "id": "jetson.status",
            "availability": AVAIL_UNSUPPORTED,
            "safety": SAFETY_READ_ONLY,
            "requires_confirmation": False,
        },
        {
            "id": "jetson.telemetry",
            "availability": AVAIL_UNSUPPORTED,
            "safety": SAFETY_READ_ONLY,
            "requires_confirmation": False,
        },
        {
            "id": "gpio.write",
            "availability": AVAIL_UNSUPPORTED,
            "safety": SAFETY_DESTRUCTIVE,
            "requires_confirmation": True,
        },
    ),
    "microcontroller": (
        {
            "id": "board.list",
            "availability": AVAIL_EXPERIMENTAL,
            "safety": SAFETY_READ_ONLY,
            "requires_confirmation": False,
        },
        {
            "id": "serial.open",
            "availability": AVAIL_EXPERIMENTAL,
            "safety": SAFETY_STATE_CHANGING,
            "requires_confirmation": False,
        },
        {
            "id": "serial.read",
            "availability": AVAIL_EXPERIMENTAL,
            "safety": SAFETY_READ_ONLY,
            "requires_confirmation": False,
        },
        {
            "id": "serial.write",
            "availability": AVAIL_EXPERIMENTAL,
            "safety": SAFETY_DESTRUCTIVE,
            "requires_confirmation": False,
        },
        {
            "id": "flash.compile",
            "availability": AVAIL_EXPERIMENTAL,
            "safety": SAFETY_READ_ONLY,
            "requires_confirmation": False,
        },
        {
            "id": "flash.upload",
            "availability": AVAIL_EXPERIMENTAL,
            "safety": SAFETY_DESTRUCTIVE,
            "requires_confirmation": True,
        },
    ),
    "siemens_logo": (
        {
            "id": "plc.discover",
            "availability": AVAIL_UNSUPPORTED,
            "safety": SAFETY_READ_ONLY,
            "requires_confirmation": False,
        },
        {
            "id": "plc.read",
            "availability": AVAIL_UNSUPPORTED,
            "safety": SAFETY_READ_ONLY,
            "requires_confirmation": False,
        },
        {
            "id": "plc.write",
            "availability": AVAIL_UNSUPPORTED,
            "safety": SAFETY_DESTRUCTIVE,
            "requires_confirmation": True,
        },
    ),
    "siemens_s7": (
        {
            "id": "plc.discover",
            "availability": AVAIL_UNSUPPORTED,
            "safety": SAFETY_READ_ONLY,
            "requires_confirmation": False,
        },
        {
            "id": "plc.read",
            "availability": AVAIL_UNSUPPORTED,
            "safety": SAFETY_READ_ONLY,
            "requires_confirmation": False,
        },
        {
            "id": "plc.write",
            "availability": AVAIL_UNSUPPORTED,
            "safety": SAFETY_DESTRUCTIVE,
            "requires_confirmation": True,
        },
    ),
    "omron": (
        {
            "id": "plc.discover",
            "availability": AVAIL_UNSUPPORTED,
            "safety": SAFETY_READ_ONLY,
            "requires_confirmation": False,
        },
        {
            "id": "plc.read",
            "availability": AVAIL_UNSUPPORTED,
            "safety": SAFETY_READ_ONLY,
            "requires_confirmation": False,
        },
        {
            "id": "plc.write",
            "availability": AVAIL_UNSUPPORTED,
            "safety": SAFETY_DESTRUCTIVE,
            "requires_confirmation": True,
        },
    ),
    "schneider": (
        {
            "id": "plc.discover",
            "availability": AVAIL_UNSUPPORTED,
            "safety": SAFETY_READ_ONLY,
            "requires_confirmation": False,
        },
        {
            "id": "plc.read",
            "availability": AVAIL_UNSUPPORTED,
            "safety": SAFETY_READ_ONLY,
            "requires_confirmation": False,
        },
        {
            "id": "plc.write",
            "availability": AVAIL_UNSUPPORTED,
            "safety": SAFETY_DESTRUCTIVE,
            "requires_confirmation": True,
        },
    ),
    "weintek_hmi": (
        {
            "id": "hmi.discover",
            "availability": AVAIL_UNSUPPORTED,
            "safety": SAFETY_READ_ONLY,
            "requires_confirmation": False,
        },
        {
            "id": "hmi.read",
            "availability": AVAIL_UNSUPPORTED,
            "safety": SAFETY_READ_ONLY,
            "requires_confirmation": False,
        },
        {
            "id": "hmi.write",
            "availability": AVAIL_UNSUPPORTED,
            "safety": SAFETY_DESTRUCTIVE,
            "requires_confirmation": True,
        },
        {
            "id": "plc.read",
            "availability": AVAIL_UNSUPPORTED,
            "safety": SAFETY_READ_ONLY,
            "requires_confirmation": False,
        },
        {
            "id": "plc.write",
            "availability": AVAIL_UNSUPPORTED,
            "safety": SAFETY_DESTRUCTIVE,
            "requires_confirmation": True,
        },
        {
            "id": "hmi.identify",
            "availability": AVAIL_UNSUPPORTED,
            "safety": SAFETY_READ_ONLY,
            "requires_confirmation": False,
        },
        {
            "id": "opcua.read",
            "availability": AVAIL_UNSUPPORTED,
            "safety": SAFETY_READ_ONLY,
            "requires_confirmation": False,
        },
        {
            "id": "opcua.write",
            "availability": AVAIL_UNSUPPORTED,
            "safety": SAFETY_DESTRUCTIVE,
            "requires_confirmation": True,
        },
        {
            "id": "mqtt.subscribe",
            "availability": AVAIL_UNSUPPORTED,
            "safety": SAFETY_STATE_CHANGING,
            "requires_confirmation": False,
        },
        {
            "id": "mqtt.publish",
            "availability": AVAIL_UNSUPPORTED,
            "safety": SAFETY_DESTRUCTIVE,
            "requires_confirmation": True,
        },
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
