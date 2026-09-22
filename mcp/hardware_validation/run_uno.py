"""Physical validation run against an Arduino Uno, driven through the real MCP server.

Not part of the pytest suite: it needs a board on /dev/ttyACM*, arduino-cli, and a
config with [flash] allow = true and the validation sketch under sketch_roots.
It flashes the board. Results go to stdout as JSON with the USB serial redacted.

    python mcp/hardware_validation/run_uno.py --launcher bin/hardware-mcp \
        --sketch ~/Arduino/hw-validation --outside-sketch /tmp/outside --out results.json
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
from pathlib import Path
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

FQBN = "arduino:avr:uno"
BAUD = 115200


class Run:
    def __init__(self, session: ClientSession, checks: list[dict[str, Any]]) -> None:
        self.session = session
        # Shared with main(), so the checks that ran are saved even if a later step raises.
        self.checks = checks
        self.serial = ""

    async def call(self, tool: str, **args: Any) -> dict[str, Any]:
        result = await self.session.call_tool(tool, args)
        if result.structured_content is not None:
            payload = result.structured_content
            if set(payload) == {"result"} and isinstance(payload["result"], dict):
                payload = payload["result"]
            return payload
        text = "".join(getattr(block, "text", "") for block in result.content)
        return json.loads(text)

    def record(self, name: str, tool: str, passed: bool, detail: Any) -> None:
        self.checks.append({"check": name, "tool": tool, "passed": passed, "detail": self.redact(detail)})
        print(f"{'PASS' if passed else 'FAIL'}  {name}", file=sys.stderr)

    def redact(self, value: Any) -> Any:
        if isinstance(value, dict):
            out = {}
            for key, item in value.items():
                if key == "upload_token" and item:
                    out[key] = "redacted"
                elif key in {"serial", "usb_serial"} and item:
                    out[key] = "redacted"
                elif key == "by_id_path" and item:
                    out[key] = "redacted"
                else:
                    out[key] = self.redact(item)
            return out
        if isinstance(value, list):
            return [self.redact(item) for item in value]
        if isinstance(value, str) and self.serial and self.serial in value:
            return value.replace(self.serial, "<usb-serial>")
        return value


def error_code(payload: dict[str, Any]) -> str | None:
    return (payload.get("error") or {}).get("code")


async def compile_uno(run: Run, sketch: str, port: str) -> dict[str, Any]:
    return await run.call("compile_sketch", sketch_dir=sketch, fqbn=FQBN, port=port)


async def upload_args(sketch: str, port: str, compiled: dict[str, Any]) -> dict[str, Any]:
    return {
        "sketch_dir": sketch,
        "port": port,
        "fqbn": FQBN,
        # A failed compile has none of these; the uploads are then refused and
        # recorded as failed checks instead of ending the run with a KeyError.
        "upload_token": compiled.get("upload_token", ""),
        "artifact_path": compiled.get("artifact_path", ""),
        "artifact_digest": compiled.get("artifact_digest", ""),
    }


async def main_async(args: argparse.Namespace, checks: list[dict[str, Any]]) -> None:
    params = StdioServerParameters(command=args.launcher, args=[])
    sketch = str(Path(args.sketch).expanduser())
    outside = str(Path(args.outside_sketch).expanduser())

    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        run = Run(session, checks)

        tools = await session.list_tools()
        names = sorted(tool.name for tool in tools.tools)
        run.record(
            "Server starts over stdio and lists tools", "list_tools", "list_boards" in names, {"tools": len(names)}
        )

        boards = await run.call("list_boards")
        unos = [b for b in boards.get("boards", []) if b.get("suggested_fqbn") == FQBN]
        uno = unos[0] if unos else {}
        run.serial = (uno.get("serial") or "").strip()
        port = uno.get("port", "/dev/ttyACM0")
        run.record("Discovery finds the Uno with FQBN arduino:avr:uno", "list_boards", len(unos) == 1, boards)
        run.record(
            "Non-board serial ports are not listed",
            "list_boards",
            all(b.get("port", "").startswith(("/dev/ttyACM", "/dev/ttyUSB")) for b in boards.get("boards", [])),
            {"ports": [b.get("port") for b in boards.get("boards", [])]},
        )

        described = await run.call("describe_board", port=port)
        run.record(
            "describe_board reports the Uno and no open session",
            "describe_board",
            described.get("ok") is True and (described.get("board") or {}).get("open_session") is None,
            described,
        )

        report = await run.call("hardware_report")
        leaked = run.serial and run.serial in json.dumps(report)
        run.record(
            "hardware_report redacts the USB serial",
            "hardware_report",
            report.get("ok") is True and not leaked,
            {
                "ok": report.get("ok"),
                "devices": len(report.get("devices", [])),
                "serial_leaked": bool(leaked),
            },
        )

        fqbns = await run.call("list_fqbns", filter="uno")
        run.record(
            "list_fqbns includes arduino:avr:uno",
            "list_fqbns",
            any(b.get("fqbn") == FQBN for b in fqbns.get("boards", [])),
            {"matches": [b.get("fqbn") for b in fqbns.get("boards", [])]},
        )

        refused_port = await run.call("serial_open", port="/dev/ttyS4", baud=BAUD)
        run.record(
            "serial_open on a non-allowlisted port is refused",
            "serial_open",
            refused_port.get("ok") is False,
            refused_port,
        )

        outside_compile = await run.call("compile_sketch", sketch_dir=outside, fqbn=FQBN, port=port)
        run.record(
            "compile_sketch outside sketch_roots is refused",
            "compile_sketch",
            outside_compile.get("ok") is False,
            outside_compile,
        )

        started = time.monotonic()
        compiled = await compile_uno(run, sketch, port)
        run.record(
            "compile_sketch produces an artifact and upload token",
            "compile_sketch",
            compiled.get("ok") is True and bool(compiled.get("upload_token")),
            {
                "ok": compiled.get("ok"),
                "artifact_digest": compiled.get("artifact_digest"),
                "has_hex": any(name.endswith(".hex") for name in os.listdir(compiled["artifact_path"]))
                if compiled.get("artifact_path")
                else False,
                "expires_in_seconds": compiled.get("expires_in_seconds"),
                "seconds": round(time.monotonic() - started, 1),
                "error": compiled.get("error"),
            },
        )
        base = await upload_args(sketch, port, compiled)

        unconfirmed = await run.call("upload_sketch", **base, confirm=False)
        run.record(
            "upload_sketch without confirm is refused",
            "upload_sketch",
            error_code(unconfirmed) == "FLASH_UNCONFIRMED",
            unconfirmed,
        )

        mismatch = await run.call("upload_sketch", **{**base, "fqbn": "arduino:avr:nano"}, confirm=True)
        run.record(
            "upload_sketch with a different FQBN is refused", "upload_sketch", mismatch.get("ok") is False, mismatch
        )

        forged = await run.call("upload_sketch", **{**base, "upload_token": "forged.token"}, confirm=True)
        run.record("upload_sketch with a forged token is refused", "upload_sketch", forged.get("ok") is False, forged)

        wrong_digest = await run.call("upload_sketch", **{**base, "artifact_digest": "0" * 64}, confirm=True)
        run.record(
            "upload_sketch with a different artifact digest is refused",
            "upload_sketch",
            wrong_digest.get("ok") is False,
            wrong_digest,
        )

        started = time.monotonic()
        uploaded = await run.call("upload_sketch", **base, confirm=True)
        run.record(
            "upload_sketch with token + confirm flashes the Uno",
            "upload_sketch",
            uploaded.get("ok") is True,
            {**uploaded, "seconds": round(time.monotonic() - started, 1)},
        )

        opened = await run.call("serial_open", port=port, baud=BAUD)
        session_id = opened.get("session_id", "")
        run.record("serial_open at 115200", "serial_open", opened.get("ok") is True and bool(session_id), opened)

        same = await run.call("serial_open", port=port, baud=BAUD)
        run.record(
            "serial_open is idempotent at the same baud",
            "serial_open",
            same.get("session_id") == session_id,
            same,
        )
        other_baud = await run.call("serial_open", port=port, baud=9600)
        run.record(
            "serial_open at a different baud is refused", "serial_open", other_baud.get("ok") is False, other_baud
        )

        banner = await run.call("serial_read", session_id=session_id, max_wait_ms=5000, until="HWVAL READY")
        run.record(
            "serial_read receives the sketch banner after reset",
            "serial_read",
            "HWVAL READY" in (banner.get("data") or ""),
            banner,
        )

        no_confirm = await run.call("serial_write", session_id=session_id, data="PING", confirm=False)
        run.record("serial_write without confirm is refused", "serial_write", no_confirm.get("ok") is False, no_confirm)

        pong = await run.call("serial_query", session_id=session_id, data="PING", wait_ms=3000, confirm=True)
        run.record("serial_query PING returns PONG", "serial_query", "PONG" in (pong.get("data") or ""), pong)

        written = await run.call("serial_write", session_id=session_id, data="omarchy", confirm=True)
        echo = await run.call("serial_read", session_id=session_id, max_wait_ms=3000, until="\n")
        run.record(
            "serial_write then serial_read round-trips through the board",
            "serial_write/serial_read",
            written.get("bytes_written") == 8 and "ECHO:omarchy" in (echo.get("data") or ""),
            {"write": written, "read": echo},
        )

        status = await run.call("serial_status", session_id=session_id)
        run.record("serial_status reports no errors", "serial_status", status.get("ok") is True, status)

        silent = await run.call("serial_read", session_id=session_id, max_wait_ms=500)
        run.record(
            "serial_read on a silent device times out instead of hanging",
            "serial_read",
            silent.get("ok") is True and silent.get("timed_out") is True,
            silent,
        )

        recompiled = await compile_uno(run, sketch, port)
        started = time.monotonic()
        with_session = await run.call("upload_sketch", **await upload_args(sketch, port, recompiled), confirm=True)
        run.record(
            "upload_sketch with a session open closes and restores it",
            "upload_sketch",
            with_session.get("ok") is True and with_session.get("session_restored") is True,
            {**with_session, "seconds": round(time.monotonic() - started, 1)},
        )
        restored = await run.call("list_sessions")
        restored_id = next((s.get("session_id") for s in restored.get("sessions", []) if s.get("port") == port), "")
        after = await run.call("serial_read", session_id=restored_id, max_wait_ms=5000, until="HWVAL READY")
        run.record(
            "Restored session reads the banner after re-flash",
            "serial_read",
            "HWVAL READY" in (after.get("data") or ""),
            after,
        )

        closed = await run.call("serial_close", session_id=restored_id)
        sessions = await run.call("list_sessions")
        run.record(
            "serial_close releases the port",
            "serial_close",
            closed.get("ok") is True and not sessions.get("sessions"),
            {"close": closed, "sessions": sessions},
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--launcher", required=True)
    parser.add_argument("--sketch", required=True)
    parser.add_argument("--outside-sketch", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    checks: list[dict[str, Any]] = []
    try:
        asyncio.run(main_async(args, checks))
    except Exception as exc:  # noqa: BLE001 - keep the checks that ran; record why the run stopped
        checks.append({"check": "Validation run completed", "tool": "-", "passed": False,
                       "detail": f"{type(exc).__name__}: {exc}"[:500]})
    result: dict[str, Any] = {"checks": checks}
    arduino_cli = os.environ.get("OMARCHY_HARDWARE_ARDUINO_CLI", "/usr/local/bin/arduino-cli")
    try:
        cli = subprocess.run(  # noqa: S603 - fixed argv, the same binary the MCP server runs
            [arduino_cli, "version"], capture_output=True, text=True, check=False, timeout=30
        ).stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        cli = f"unavailable ({type(exc).__name__})"
    result["environment"] = {
        "date": time.strftime("%Y-%m-%d"),
        "kernel": platform.release(),
        "python": platform.python_version(),
        "arduino_cli": cli,
    }
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    failed = [c["check"] for c in result["checks"] if not c["passed"]]
    print(f"{len(result['checks']) - len(failed)}/{len(result['checks'])} passed", file=sys.stderr)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
