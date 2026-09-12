"""Project-owned capability reference for future hardware-standard adapters.

This is not an MHS schema or driver. Exporting metadata never opens a device,
contacts a host, or authorizes an operation.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from typing import Any

from . import __version__, errors, support
from .config import Config, ConfigError, load
from .errors import ToolError, ok

# Bind existing family operations to their guarded MCP entry points. A missing
# binding must stay explicit so a future adapter cannot invent an implementation.
TOOL_BINDINGS: dict[str, tuple[str, ...]] = {
    "board.list": ("list_boards",),
    "serial.open": ("serial_open",),
    "serial.read": ("serial_read",),
    "serial.write": ("serial_write", "serial_query"),
    "flash.compile": ("compile_sketch",),
    "flash.upload": ("upload_sketch",),
    "pi.inventory": ("pi_inventory",),
    "pi.status": ("pi_status",),
    "gpio.list": ("gpio_list_pins",),
    "gpio.read": ("gpio_read_pin",),
    "gpio.set_mode": ("gpio_set_mode",),
    "gpio.write": ("gpio_write_pin",),
    "jetson.inventory": ("jetson_inventory",),
    "jetson.status": ("jetson_status",),
    "opcua.read": ("weintek_opcua_read",),
    "opcua.write": ("weintek_opcua_write",),
    "mqtt.publish": ("weintek_mqtt_publish",),
}


def export_reference(config: Config, family: str | None = None) -> dict[str, Any]:
    """Describe implementations and policy without exporting target identities.

    Availability comes from the existing support matrix. It is not a claim that
    hardware is connected, configured, validated, or safe for a particular load.
    """
    families = {
        name: [{**row, "mcp_tools": list(TOOL_BINDINGS.get(row["id"], ()))} for row in rows]
        for name, rows in support.export(family).items()
    }
    return {
        "format": "omarchy-hardware-reference",
        "format_version": 1,
        "package_version": __version__,
        "mhs": {
            "status": "preparation_only",
            "compatible": False,
            "specification_version": None,
            "reference": "docs/mhs-readiness.md",
        },
        "scope": "family capabilities and local policy; not live device discovery",
        "families": families,
        "policy_snapshot": {
            "serial": {
                "max_write_bytes": config.max_write_bytes,
                "write_budget_bytes_per_min": config.write_budget_bytes_per_min,
                "allow_unknown": config.allow_unknown_serial,
                "writes_require_confirmation": True,
            },
            "flash": {
                "allow": config.allow_flash,
                "sketch_roots_configured": bool(config.sketch_roots),
                "upload_requires": [
                    "confirmation",
                    "compile_token",
                    "board_fqbn_match",
                    "usb_serial_match_when_present",
                    "allowed_sketch_root",
                    "writable_audit_log",
                ],
            },
            "pi": {
                "hosts_configured": bool(config.pi_hosts),
                "allowed_bcm_pins": list(config.pi_allowed_pins),
                "ssh_timeout_seconds": config.pi_ssh_timeout,
            },
            "jetson": {"hosts_configured": bool(config.jetson_hosts)},
        },
        "limitations": [
            "The policy snapshot is informational; existing tools recheck policy at execution.",
            "confirm=true is model-controlled and is not proof of human approval.",
            "Physical voltage, current, load and motion limits are unspecified; do not infer them from USB IDs.",
            "No emergency-stop or physical-interlock implementation is provided.",
            "Board mappings still need review; see docs/mhs-readiness.md before enabling firmware operations.",
            "Experimental and unsupported operations retain their support-matrix status.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the local hardware capability reference; no device I/O.")
    parser.add_argument("--family", choices=support.FAMILIES)
    args = parser.parse_args()
    try:
        payload = ok(reference=export_reference(load(), args.family))
    except ToolError as exc:
        payload = exc.as_result()
    except (ConfigError, OSError, tomllib.TOMLDecodeError) as exc:
        payload = ToolError(errors.CONFIG_ERROR, str(exc)).as_result()
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    if not payload["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
