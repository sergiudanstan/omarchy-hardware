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

from . import __version__, board_profiles, errors, support
from .config import Config, ConfigError, load
from .errors import ToolError, ok

# Bind existing family operations to their guarded MCP entry points. A missing
# binding must stay explicit so a future adapter cannot invent an implementation.
TOOL_BINDINGS: dict[str, tuple[str, ...]] = {
    "board.list": ("list_boards",),
    "board.describe": ("describe_board",),
    "board.profile": ("board_profile",),
    "board.wiring_check": ("wiring_check",),
    "board.history": ("board_history",),
    "board.label": ("board_label",),
    "board.assign_profile": ("board_profile_assign",),
    "board.decode_crash": ("decode_crash",),
    "board.identify_i2c": ("identify_i2c",),
    "board.list_fqbns": ("list_fqbns",),
    "device.reference": ("device_reference",),
    "display.reference": ("display_reference",),

    "board.probe": ("board_probe",),
    "board.fingerprint": ("fingerprint_board",),
    "serial.open": ("serial_open",),
    "serial.read": ("serial_read", "serial_expect"),
    "serial.write": ("serial_write", "serial_query"),
    "flash.compile": ("compile_sketch",),
    "flash.upload": ("upload_sketch",),
    "firmware.backup": ("firmware_backup", "firmware_backups"),
    "firmware.restore": ("firmware_restore",),
    "micropython.exec": ("mpy_exec",),
    "micropython.files": ("mpy_list",),
    "micropython.put": ("mpy_put",),
    "pi.discover": ("pi_discover",),
    "pi.inventory": ("pi_inventory",),
    "pi.status": ("pi_status",),
    "gpio.list": ("gpio_list_pins",),
    "gpio.read": ("gpio_read_pin",),
    "gpio.set_mode": ("gpio_set_mode",),
    "gpio.write": ("gpio_write_pin",),
    "jetson.inventory": ("jetson_inventory",),
    "jetson.status": ("jetson_status",),
    "hmi.identify": ("weintek_hmi_identify",),
    "opcua.read": ("weintek_opcua_read",),
    "opcua.write": ("weintek_opcua_write",),
    "mqtt.publish": ("weintek_mqtt_publish",),
    "mqtt.subscribe": ("weintek_mqtt_subscribe",),
    "modbus.read": ("weintek_modbus_read",),
    "ming.status": ("ming_status",),
    "ming.mqtt.subscribe": ("mqtt_subscribe",),
    "ming.mqtt.publish": ("mqtt_publish",),
    "ming.mqtt.bridge": ("serial_bridge_start", "serial_bridge_status", "serial_bridge_stop"),
    "ming.influx.measurements": ("influx_measurements",),
    "ming.influx.query": ("influx_query",),
    "ming.influx.write": ("influx_write",),
    "ming.nodered.flows": ("nodered_flows",),
    "ming.nodered.inject": ("nodered_inject",),
    "ming.grafana.dashboards": ("grafana_dashboards",),
    "ming.grafana.annotate": ("grafana_annotate",),
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
        "format_version": 2,
        "package_version": __version__,
        "mhs": {
            "status": "preparation_only",
            "compatible": False,
            "specification_version": None,
            "reference": "docs/mhs-readiness.md",
        },
        "scope": "family capabilities and local policy; not live device discovery",
        "families": families,
        "devices": [{key: p[key] for key in ("id", "name", "chip", "display")}
                    for p in board_profiles.index()],
        "policy_snapshot": {
            "serial": {
                "max_write_bytes": config.max_write_bytes,
                "write_budget_bytes_per_min": config.write_budget_bytes_per_min,
                "allow_unknown": config.allow_unknown_serial,
                "writes_require_confirmation": True,
            },
            "flash": {
                "allow": config.allow_flash,
                "default_allow": False,
                "max_uploads_per_hour": config.max_uploads_per_hour,
                "sketch_roots_configured": bool(config.sketch_roots),
                "upload_requires": [
                    "confirmation",
                    "compile_token",
                    "board_fqbn_match",
                    "flash_budget",
                    "identified_board_with_live_chip_probe_for_native_usb",
                    "usb_serial_match_when_present",
                    "allowed_sketch_root",
                    "writable_audit_log",
                ],
            },
            "pi": {
                "hosts_configured": bool(config.pi_hosts),
                "allowed_bcm_pins": list(config.pi_allowed_pins),
                "ssh_timeout_seconds": config.pi_ssh_timeout,
                "actuation_budget_per_min": config.actuation_budget_per_min,
                "writes_require_confirmation": True,
            },
            "jetson": {"hosts_configured": bool(config.jetson_hosts)},
            "ming": {
                "allow": config.ming_allow,
                "targets_configured": {
                    "mqtt": len(config.ming_mqtt),
                    "influxdb": len(config.ming_influxdb),
                    "nodered": len(config.ming_nodered),
                    "grafana": len(config.ming_grafana),
                },
                "timeout_seconds": config.ming_timeout,
                "max_payload_bytes": config.ming_max_payload_bytes,
                "write_budget_per_min": config.ming_write_budget_per_min,
                "writes_require_confirmation": True,
                "raw_flux_accepted": False,
                "nodered_flow_deploy": False,
            },
            "audit": {
                "scope": "every operation that changes physical state",
                "recorded_before_the_operation": True,
                "refuses_when_unwritable": True,
                "chained": "sha256",
                "payloads": "length and digest only, never content",
            },
        },
        "limitations": [
            "The policy snapshot is informational; existing tools recheck policy at execution.",
            "confirm=true is model-controlled and is not proof of human approval.",
            "Electrical limits come from sourced device profiles; "
            "connected loads and motion limits remain unspecified.",
            "No emergency-stop or physical-interlock implementation is provided.",
            "The audit log is tamper-evident, not tamper-proof: it is a local file owned by the user.",
            "A profile binding is user-selected metadata, never authorization; live identity checks still apply.",
            "Experimental and unsupported operations retain their support-matrix status.",
        ],
    }


def device_reference(profile_id: str, config: Config | None = None) -> dict[str, Any]:
    """Sourced device capabilities and policy, not live measurements or authorization."""
    profile = board_profiles.export(profile_id)
    common = export_reference(load() if config is None else config, profile["family"])
    measures: list[dict[str, Any]] = [{
        "kind": "adc", "pins": [p["name"] for p in profile["pins"] if "analog_in" in p["caps"]],
    }]
    adjusts: list[dict[str, Any]] = [{
        "kind": "gpio", "digital_output_count": sum(
            "digital" in p["caps"] and "input_only" not in p["caps"] for p in profile["pins"]),
        "pwm_output_count": sum("pwm" in p["caps"] for p in profile["pins"]),
        "note": "Capability counts include reserved/caution pins; consult limits before wiring.",
    }]
    for peripheral in profile.get("peripherals", []):
        kind = peripheral["kind"]
        if kind in {"battery", "sensor", "button", "touch"}:
            measures.append(dict(peripheral))
        if kind in {"display", "led", "rgb_led"}:
            adjusts.append(dict(peripheral))
        if kind == "display" and "backlight" in peripheral.get("pins", {}):
            adjusts.append({"kind": "backlight", "name": peripheral["name"],
                            "gpio": peripheral["pins"]["backlight"],
                            "active": peripheral["backlight_active"]})
    return {
        "format": "omarchy-hardware-device-reference", "format_version": 1,
        "device": {key: profile[key] for key in
                   ("id", "name", "family", "mcu", "identity", "build", "validation") if key in profile},
        "measures": measures, "adjusts": adjusts,
        "limits": {**{key: profile[key] for key in
                      ("logic_voltage", "five_volt_tolerant", "current_ma", "reserved_pins", "caution_pins")},
                   "enforced_policy": common["policy_snapshot"],
                   "note": "Sourced electrical limits are wiring guidance, not physical interlocks. "
                           "Flashing is off unless the user enables it; uploads need confirm=true, a compile "
                           "token, flash budget and identified board. Serial writes are budgeted."},
        "operations": common["families"][profile["family"]],
        "access": {
            "mcp": {"tool": "device_reference", "profile_id": profile_id,
                    "resource": f"hardware://devices/{profile_id}"},
            "cli": f"python -m omarchy_hardware reference --device {profile_id}",
            "python": f"from omarchy_hardware.reference import device_reference; device_reference({profile_id!r})",
            "note": "These interfaces return reference facts only. Use guarded MCP tools for device operations.",
        },
        "sources": profile["sources"], "data_status": profile["data_status"],
    }


def render_markdown(reference: dict[str, Any]) -> str:
    """Render all reference fields as a compact, readable Markdown file."""
    title = reference.get("device", {}).get("name", "Hardware capability reference")
    lines = [f"# {title}", ""]

    def inline(value: Any) -> str:
        if isinstance(value, dict):
            return "; ".join(f"{key}: {inline(item)}" for key, item in value.items()) or "{}"
        if isinstance(value, list):
            return ", ".join(inline(item) for item in value) or "[]"
        return value if isinstance(value, str) else json.dumps(value)

    def render(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                lines.append(f"- **{key.replace('_', ' ')}**: {inline(item)}")
        else:
            for item in value:
                lines.append(f"- {inline(item)}")

    for key, value in reference.items():
        lines.extend([f"## {key.replace('_', ' ').title()}", ""])
        if isinstance(value, (dict, list)):
            render(value)
        else:
            lines.append(str(value))
        lines.append("")
    return "\n".join(lines)


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
