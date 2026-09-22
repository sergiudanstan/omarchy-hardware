"""Extended physical validation on an Arduino Uno: every Uno-applicable tool run_uno.py does not cover.

Not part of the pytest suite. It needs the Uno on /dev/ttyACM*, arduino-cli with
arduino:avr, and [flash] allow = true with ~/Arduino under sketch_roots. It flashes
the board twice: the read-only I2C bench probe, then the validation sketch again,
so the board ends where run_uno.py leaves it.

With --ming it also bridges the probe's serial output to MQTT. That needs the
MING stack running, and the topic in [[ming.mqtt]] publish and covered by
subscribe, and allowed by the broker's own ACL (the example stack lets the
claude user read and write actuators/#). What crosses is the probe's own
diagnostic JSON, never a sensor reading.

    python mcp/hardware_validation/run_uno_extended.py --launcher bin/hardware-mcp \
        --validation-sketch ~/Arduino/hw-validation --probe-sketch ~/Arduino/omarchy_probe \
        [--ming --topic actuators/uno-probe] --out uno-extended.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from run_uno import BAUD, FQBN, Run, error_code

LABEL = "claude-validation-uno"
AUDIT_LOG = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "omarchy-hardware" / "audit.log"


def audit_events(since: float) -> list[dict[str, Any]]:
    """Audit records written after `since`, read straight from the log (hashes dropped)."""
    events = []
    try:
        lines = AUDIT_LOG.read_text(encoding="utf-8").splitlines()
    except OSError:
        return events
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("at", 0) >= since:
            events.append({k: v for k, v in entry.items() if k not in {"hash", "prev"}})
    return events


async def flash(run: Run, sketch: str, port: str, what: str) -> dict[str, Any]:
    compiled = await run.call("compile_sketch", sketch_dir=sketch, fqbn=FQBN, port=port)
    started = time.monotonic()
    uploaded = await run.call(
        "upload_sketch", sketch_dir=sketch, port=port, fqbn=FQBN,
        upload_token=compiled.get("upload_token", ""), artifact_path=compiled.get("artifact_path", ""),
        artifact_digest=compiled.get("artifact_digest", ""), confirm=True,
    )
    run.record(f"compile and flash the {what}", "compile_sketch + upload_sketch",
               compiled.get("ok") is True and uploaded.get("ok") is True,
               {"compile_ok": compiled.get("ok"), "upload": uploaded, "seconds": round(time.monotonic() - started, 1)})
    return uploaded


async def main_async(args: argparse.Namespace, checks: list[dict[str, Any]], diagnostics: dict[str, Any]) -> None:
    started_at = time.time()
    params = StdioServerParameters(command=args.launcher, args=[])
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        run = Run(session, checks)

        boards = await run.call("list_boards")
        unos = [b for b in boards.get("boards", []) if b.get("suggested_fqbn") == FQBN]
        run.record("Exactly one Uno is connected", "list_boards", len(unos) == 1,
                   {"ports": [b.get("port") for b in unos]})
        if len(unos) != 1:
            return
        run.serial = (unos[0].get("serial") or "").strip()
        port = unos[0]["port"]

        # ---- identity and knowledge (no board I/O)
        described = await run.call("describe_board", port=port)
        board = described.get("board") or {}
        original_label = board.get("label") or ""
        run.record("describe_board maps the Uno to the arduino_uno_r3 profile", "describe_board",
                   board.get("profile_id") == "arduino_uno_r3", {"profile_id": board.get("profile_id")})

        profile = await run.call("board_profile", port=port)
        pins = (profile.get("profile") or {}).get("pins") or []
        run.record("board_profile returns the Uno pin map", "board_profile",
                   profile.get("ok") is True and (profile.get("profile") or {}).get("id") == "arduino_uno_r3"
                   and len(pins) >= 20, {"id": (profile.get("profile") or {}).get("id"), "pins": len(pins)})

        reference = await run.call("get_hardware_reference")
        run.record("get_hardware_reference describes the families", "get_hardware_reference",
                   reference.get("ok") is True, {"ok": reference.get("ok")})
        capabilities = await run.call("list_capabilities")
        run.record("list_capabilities answers", "list_capabilities", capabilities.get("ok") is True,
                   {"ok": capabilities.get("ok")})
        inventory = await run.call("parts_inventory")
        run.record("parts_inventory answers (an empty inventory is valid)", "parts_inventory",
                   inventory.get("ok") is True, {"parts": len(inventory.get("parts") or [])})

        led = [{"pin": "D13", "part": "led", "role": "digital_out", "load": "led", "current_ma": 10}]
        good = await run.call("wiring_check", connections=led, port=port)
        run.record("wiring_check accepts an LED on D13", "wiring_check",
                   good.get("ok") is True and good.get("verdict") in {"pass", "check"}, good)
        motor = [{"pin": "D9", "part": "dc motor", "role": "digital_out", "load": "motor", "current_ma": 500}]
        bad = await run.call("wiring_check", connections=motor, port=port)
        run.record("wiring_check fails a motor driven straight from a pin", "wiring_check",
                   bad.get("ok") is True and bad.get("verdict") == "fail", bad)

        # Labels are read back through describe_board and board_history; list_boards
        # does not carry them (the bar panel adds them itself).
        labelled = await run.call("board_label", port=port, label=LABEL)
        redescribed = await run.call("describe_board", port=port)
        history_label = ((await run.call("board_history", port=port)).get("history") or {}).get("label")
        shown = (redescribed.get("board") or {}).get("label")
        run.record("board_label is stored and read back by describe_board and board_history", "board_label",
                   labelled.get("ok") is True and shown == LABEL and history_label == LABEL,
                   {"describe_board": shown, "board_history": history_label})

        # ---- refusals that must hold on a Uno
        backup = await run.call("firmware_backup", port=port)
        run.record("firmware_backup is refused on a non-ESP32 board", "firmware_backup",
                   error_code(backup) == "UNSUPPORTED_OPERATION", backup)
        crash = await run.call("decode_crash", port=port, crash_text="HWVAL READY\nPONG\n")
        run.record("decode_crash rejects text that is not a crash report", "decode_crash",
                   error_code(crash) == "INVALID_ARGUMENT", crash)
        vanished = await run.call("upload_sketch", sketch_dir=args.validation_sketch, port="/dev/ttyACM97",
                                  fqbn=FQBN, upload_token="not-a-token", confirm=True)  # noqa: S106 - deliberately invalid
        run.record("upload_sketch to a port that is not there says not connected", "upload_sketch",
                   error_code(vanished) == "PORT_NOT_FOUND", vanished)

        # ---- bench probe: flash, read its report, identify what is on the bus
        await flash(run, args.probe_sketch, port, "read-only I2C bench probe")
        opened = await run.call("serial_open", port=port, baud=BAUD)
        session_id = opened.get("session_id", "")
        report = await run.call("serial_expect", session_id=session_id, match='"probe":"done"', max_wait_ms=15000)
        probe: dict[str, Any] = {}
        try:
            probe = json.loads(report.get("line") or "{}")
        except ValueError:
            probe = {}
        run.record("serial_expect catches the probe report", "serial_expect",
                   report.get("matched") is True and probe.get("probe") == "done", report)
        devices = probe.get("i2c") if isinstance(probe.get("i2c"), list) else []
        diagnostics["probe_report"] = {"bus": probe.get("bus"), "i2c_devices": devices,
                                       "kind": "diagnostic probe output, not a sensor reading"}
        identified = await run.call("identify_i2c", devices=devices)
        run.record("identify_i2c accepts the probe's device list", "identify_i2c",
                   identified.get("ok") is True, {"devices": identified.get("devices"), "reported": len(devices)})

        missed = await run.call("serial_expect", session_id=session_id, match="NEVER-PRINTED", max_wait_ms=1500)
        run.record("serial_expect times out cleanly when nothing matches", "serial_expect",
                   missed.get("ok") is True and missed.get("matched") is False, missed)
        cleared = await run.call("serial_clear", session_id=session_id)
        listed_sessions = await run.call("list_sessions")
        status = await run.call("serial_status", session_id=session_id)
        run.record("serial_clear, list_sessions and serial_status agree on one open session",
                   "serial_clear/list_sessions/serial_status",
                   cleared.get("ok") is True and len(listed_sessions.get("sessions") or []) == 1
                   and status.get("ok") is True and not status.get("errors"),
                   {"sessions": len(listed_sessions.get("sessions") or []), "errors": status.get("errors")})

        not_python = await run.call("mpy_list", session_id=session_id)
        run.record("mpy_list fails cleanly on an Arduino (no MicroPython)", "mpy_list",
                   not_python.get("ok") is False, not_python)

        # ---- MING: serial -> MQTT bridge with the probe's JSON lines
        if args.ming:
            status_all = await run.call("ming_status")
            mqtt = [t for t in status_all.get("mqtt", []) if t.get("name") == args.broker]
            run.record("ming_status reaches the MQTT broker", "ming_status",
                       bool(mqtt) and mqtt[0].get("reachable") is True, {"mqtt": mqtt})
            await run.call("serial_clear", session_id=session_id)
            bridged = await run.call("serial_bridge_start", session_id=session_id, topic=args.topic,
                                     broker=args.broker, duration_s=90, min_interval_ms=1000, confirm=True)
            bridge_id = bridged.get("bridge_id", "")
            run.record("serial_bridge_start forwards the session to MQTT", "serial_bridge_start",
                       bridged.get("ok") is True and bool(bridge_id), bridged)
            owned = await run.call("serial_read", session_id=session_id, max_wait_ms=200)
            run.record("A bridged session's input is refused to serial_read", "serial_read",
                       error_code(owned) == "PORT_BUSY", owned)
            heard = await run.call("mqtt_subscribe", topic_filter=args.topic, broker=args.broker,
                                   seconds=15, max_messages=10)
            payloads = []
            for message in heard.get("messages", []):
                try:
                    payloads.append(json.loads(message.get("payload") or ""))
                except (TypeError, ValueError):
                    payloads.append(message.get("payload"))
            diagnostics["mqtt_messages"] = {"topic": args.topic, "received": payloads,
                                            "kind": "diagnostic probe output, not a sensor reading"}
            run.record("mqtt_subscribe receives the probe's JSON from the bridge", "mqtt_subscribe",
                       heard.get("ok") is True
                       and any(isinstance(p, dict) and ("probe" in p or "selftest" in p or "fw" in p)
                               for p in payloads),
                       {"messages": len(payloads)})
            counted = await run.call("serial_bridge_status")
            mine = [b for b in counted.get("bridges", []) if b.get("bridge_id") == bridge_id]
            run.record("serial_bridge_status counts what it published", "serial_bridge_status",
                       bool(mine) and mine[0].get("published", 0) >= 1, {"bridge": mine})
            stopped = await run.call("serial_bridge_stop", bridge_id=bridge_id)
            run.record("serial_bridge_stop ends the bridge and keeps the session", "serial_bridge_stop",
                       stopped.get("ok") is True and stopped.get("running") is False, stopped)

        closed = await run.call("serial_close", session_id=session_id)
        fingerprinted = await run.call("fingerprint_board", port=port)
        firmware = [m for m in fingerprinted.get("matches", []) if m.get("kind") == "omarchy_firmware"]
        run.record("fingerprint_board recognises the probe's firmware banner", "fingerprint_board",
                   closed.get("ok") is True and bool(firmware) and firmware[0].get("fw") == "omarchy_probe",
                   {"matches": fingerprinted.get("matches")})

        # ---- leave the board as found: the validation sketch, answering PING
        await flash(run, args.validation_sketch, port, "validation sketch again")
        reopened = await run.call("serial_open", port=port, baud=BAUD)
        final_id = reopened.get("session_id", "")
        banner = await run.call("serial_read", session_id=final_id, max_wait_ms=5000, until="HWVAL READY")
        pong = await run.call("serial_query", session_id=final_id, data="PING", confirm=True)
        await run.call("serial_close", session_id=final_id)
        run.record("The board is back on the validation sketch and answers PING", "serial_read + serial_query",
                   "HWVAL READY" in (banner.get("data") or "") and "PONG" in (pong.get("data") or ""),
                   {"banner": banner.get("data"), "reply": pong.get("data")})

        history = await run.call("board_history", port=port)
        uploads = (history.get("history") or {}).get("uploads") or []
        run_start_iso = datetime.fromtimestamp(started_at, UTC).isoformat(timespec="seconds")
        recent = [u for u in uploads if (u.get("at") or "") >= run_start_iso]
        run.record("board_history records this run's two uploads", "board_history",
                   len(recent) >= 2 and {u.get("sketch") for u in recent} >= {"omarchy_probe", "hw-validation"},
                   {"recent": recent})

        restored = await run.call("board_label", port=port, label=original_label)
        run.record("The board's original label is restored", "board_label", restored.get("ok") is True,
                   {"label": original_label or None})

        audit = await run.call("audit_status")
        events = audit_events(started_at)
        started = sum(1 for e in events if e.get("event") == "upload_started")
        finished = sum(1 for e in events if e.get("event") == "upload_finished")
        bridge_events = [e.get("event") for e in events if e.get("event", "").startswith("serial_bridge")]
        run.record("Audit chain intact; every upload_started has its upload_finished", "audit_status",
                   audit.get("ok") is True and started == finished >= 2,
                   {"chain_ok": audit.get("ok"), "records": audit.get("records"),
                    "upload_started": started, "upload_finished": finished})
        if args.ming:
            run.record("The bridge's start and stop are both in the audit log", "audit log",
                       bridge_events == ["serial_bridge_started", "serial_bridge_stopped"],
                       {"events": bridge_events})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--launcher", required=True)
    parser.add_argument("--validation-sketch", required=True)
    parser.add_argument("--probe-sketch", required=True)
    parser.add_argument("--ming", action="store_true")
    parser.add_argument("--broker", default="stack")
    parser.add_argument("--topic", default="actuators/uno-probe")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    args.validation_sketch = str(Path(args.validation_sketch).expanduser())
    args.probe_sketch = str(Path(args.probe_sketch).expanduser())

    checks: list[dict[str, Any]] = []
    diagnostics: dict[str, Any] = {}
    try:
        asyncio.run(main_async(args, checks, diagnostics))
    except Exception as exc:  # noqa: BLE001 - keep the checks that ran; record why the run stopped
        checks.append({"check": "Validation run completed", "tool": "-", "passed": False,
                       "detail": f"{type(exc).__name__}: {exc}"[:500]})
    arduino_cli = os.environ.get("OMARCHY_HARDWARE_ARDUINO_CLI", "/usr/local/bin/arduino-cli")
    try:
        cli = subprocess.run(  # noqa: S603 - fixed argv, the same binary the MCP server runs
            [arduino_cli, "version"], capture_output=True, text=True, check=False, timeout=30
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        cli = f"unavailable ({type(exc).__name__})"
    result = {
        "checks": checks,
        "diagnostics": diagnostics,
        "not_tested": [
            "Exhausting the hourly flash budget (30 uploads)",
            "Unplug and replug (needs a person at the board)",
            "I2C identification against real devices (nothing is wired to the Uno)",
            "ESP32 backup/restore/crash decode, Pico UF2, Raspberry Pi GPIO",
        ],
        "environment": {
            "date": time.strftime("%Y-%m-%d"),
            "kernel": platform.release(),
            "python": platform.python_version(),
            "arduino_cli": cli,
            "ming": args.ming,
        },
    }
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    failed = [c["check"] for c in checks if not c["passed"]]
    print(f"{len(checks) - len(failed)}/{len(checks)} passed", file=sys.stderr)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
