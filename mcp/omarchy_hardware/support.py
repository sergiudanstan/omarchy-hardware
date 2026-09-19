"""Honest hardware support matrix.

Availability is independent of safety:

- supported: implemented and intended for use
- experimental: implemented, but not physically validated
- unsupported: not implemented; tools must not pretend otherwise

A family row can stay experimental while `supported_boards` lists the exact FQBNs
it was physically validated on (see docs/hardware-validation.md).
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
    "ming_stack",
)


class SupportRow(TypedDict):
    id: str
    availability: str
    safety: str
    requires_confirmation: bool
    supported_boards: list[str]


def _row(
    op_id: str,
    availability: str,
    safety: str,
    confirm: bool = False,
    boards: tuple[str, ...] = (),
) -> SupportRow:
    return {
        "id": op_id,
        "availability": availability,
        "safety": safety,
        "requires_confirmation": confirm,
        "supported_boards": list(boards),
    }


# Boards with a passing physical validation row per operation.
UNO = ("arduino:avr:uno",)


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
        _row("pi.discover", AVAIL_EXPERIMENTAL, SAFETY_READ_ONLY),
        _row("gpio.list", AVAIL_EXPERIMENTAL, SAFETY_READ_ONLY),
        _row("gpio.read", AVAIL_EXPERIMENTAL, SAFETY_READ_ONLY),
        _row("gpio.set_mode", AVAIL_EXPERIMENTAL, SAFETY_STATE_CHANGING, True),
        _row("gpio.write", AVAIL_EXPERIMENTAL, SAFETY_DESTRUCTIVE, True),
        _row("gpio.pwm", AVAIL_UNSUPPORTED, SAFETY_STATE_CHANGING, True),
        _row("gpio.spi", AVAIL_UNSUPPORTED, SAFETY_STATE_CHANGING, True),
        _row("gpio.i2c", AVAIL_UNSUPPORTED, SAFETY_STATE_CHANGING, True),
    ),
    "jetson": (
        _row("jetson.inventory", AVAIL_EXPERIMENTAL, SAFETY_READ_ONLY),
        _row("jetson.status", AVAIL_EXPERIMENTAL, SAFETY_READ_ONLY),
        _row("jetson.telemetry", AVAIL_UNSUPPORTED, SAFETY_READ_ONLY),
    ),
    "microcontroller": (
        _row("board.list", AVAIL_EXPERIMENTAL, SAFETY_READ_ONLY, boards=UNO),
        _row("board.fingerprint", AVAIL_EXPERIMENTAL, SAFETY_STATE_CHANGING),
        _row("serial.open", AVAIL_EXPERIMENTAL, SAFETY_STATE_CHANGING, boards=UNO),
        _row("serial.read", AVAIL_EXPERIMENTAL, SAFETY_READ_ONLY, boards=UNO),
        _row("serial.write", AVAIL_EXPERIMENTAL, SAFETY_DESTRUCTIVE, True, boards=UNO),
        _row("flash.compile", AVAIL_EXPERIMENTAL, SAFETY_READ_ONLY, boards=UNO),
        _row("flash.upload", AVAIL_EXPERIMENTAL, SAFETY_DESTRUCTIVE, True, boards=UNO),
        _row("firmware.backup", AVAIL_EXPERIMENTAL, SAFETY_STATE_CHANGING),
        _row("firmware.restore", AVAIL_EXPERIMENTAL, SAFETY_DESTRUCTIVE, True),
        _row("micropython.exec", AVAIL_EXPERIMENTAL, SAFETY_DESTRUCTIVE, True),
        _row("micropython.files", AVAIL_EXPERIMENTAL, SAFETY_STATE_CHANGING),
        _row("micropython.put", AVAIL_EXPERIMENTAL, SAFETY_DESTRUCTIVE, True),
    ),
    "siemens_logo": _plc_unsupported(),
    "siemens_s7": _plc_unsupported(),
    "omron": _plc_unsupported(),
    "schneider": _plc_unsupported(),
    "weintek_hmi": (
        _row("hmi.identify", AVAIL_EXPERIMENTAL, SAFETY_READ_ONLY),
        _row("opcua.read", AVAIL_EXPERIMENTAL, SAFETY_READ_ONLY),
        _row("opcua.write", AVAIL_EXPERIMENTAL, SAFETY_DESTRUCTIVE, True),
        _row("mqtt.subscribe", AVAIL_EXPERIMENTAL, SAFETY_READ_ONLY),
        _row("modbus.read", AVAIL_EXPERIMENTAL, SAFETY_READ_ONLY),
        _row("mqtt.publish", AVAIL_EXPERIMENTAL, SAFETY_DESTRUCTIVE, True),
    ),
    # MQTT, InfluxDB, Node-RED, Grafana. Experimental: exercised against fake
    # servers in the test suite and end to end against examples/ming-stack on
    # x86_64, which is not the same as a validated deployment on a Pi.
    "ming_stack": (
        _row("ming.status", AVAIL_EXPERIMENTAL, SAFETY_READ_ONLY),
        _row("ming.mqtt.subscribe", AVAIL_EXPERIMENTAL, SAFETY_READ_ONLY),
        _row("ming.mqtt.publish", AVAIL_EXPERIMENTAL, SAFETY_DESTRUCTIVE, True),
        _row("ming.mqtt.bridge", AVAIL_EXPERIMENTAL, SAFETY_DESTRUCTIVE, True),
        _row("ming.influx.measurements", AVAIL_EXPERIMENTAL, SAFETY_READ_ONLY),
        _row("ming.influx.query", AVAIL_EXPERIMENTAL, SAFETY_READ_ONLY),
        _row("ming.influx.write", AVAIL_EXPERIMENTAL, SAFETY_STATE_CHANGING, True),
        _row("ming.nodered.flows", AVAIL_EXPERIMENTAL, SAFETY_READ_ONLY),
        _row("ming.nodered.inject", AVAIL_EXPERIMENTAL, SAFETY_DESTRUCTIVE, True),
        _row("ming.nodered.deploy", AVAIL_UNSUPPORTED, SAFETY_DESTRUCTIVE, True),
        _row("ming.grafana.dashboards", AVAIL_EXPERIMENTAL, SAFETY_READ_ONLY),
        _row("ming.grafana.annotate", AVAIL_EXPERIMENTAL, SAFETY_STATE_CHANGING, True),
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
        "supported_boards": [],
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
