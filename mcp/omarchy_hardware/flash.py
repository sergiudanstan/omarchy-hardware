"""Compiling and uploading sketches via arduino-cli.

Uploading is the only genuinely destructive operation here, so it is gated twice:
the caller must present a token minted by a *successful compile in this process*,
and must pass confirm=True. That makes "flash the board" impossible to reach in a
single unconsidered tool call.
"""

from __future__ import annotations

import base64
import hmac
import json
import os
import secrets
import subprocess
import time
from hashlib import sha256
from pathlib import Path
from typing import Any

from . import errors
from .config import STATE_DIR
from .errors import ToolError

TOKEN_TTL_SECONDS = 300
_SECRET = secrets.token_bytes(32)
ARDUINO_CLI = os.environ.get("OMARCHY_HARDWARE_ARDUINO_CLI", "/usr/bin/arduino-cli")

if not os.path.isabs(ARDUINO_CLI):
    raise RuntimeError("OMARCHY_HARDWARE_ARDUINO_CLI must be an absolute path")


def _arduino_cli(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    try:
        # S603: an argv list with shell=False, never a shell string. Every element of
        # `args` is either a literal verb or a value already validated upstream (the
        # port by policy.resolve_port, the sketch dir by resolve_sketch_dir).
        return subprocess.run(  # noqa: S603
            [ARDUINO_CLI, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ToolError(
            errors.TOOL_MISSING,
            "arduino-cli is not installed.",
            "Run the plugin's bin/setup.sh to install it.",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ToolError(errors.SERIAL_ERROR, "arduino-cli timed out.") from exc


def _tail(text: str, lines: int = 40) -> str:
    return "\n".join((text or "").strip().splitlines()[-lines:])


def _token_payload(sketch_dir: str, fqbn: str, serial: str, expiry: int) -> bytes:
    return json.dumps([sketch_dir, fqbn, serial, expiry], separators=(",", ":")).encode()


def mint_token(sketch_dir: str, fqbn: str, serial: str = "") -> str:
    expiry = int(time.time()) + TOKEN_TTL_SECONDS
    digest = hmac.new(_SECRET, _token_payload(sketch_dir, fqbn, serial, expiry), sha256).digest()
    return f"{base64.urlsafe_b64encode(digest).decode().rstrip('=')}.{expiry}"


def verify_token(token: str, sketch_dir: str, fqbn: str, serial: str = "") -> None:
    try:
        signature, expiry_text = token.rsplit(".", 1)
        expiry = int(expiry_text)
    except (ValueError, AttributeError) as exc:
        raise ToolError(errors.INVALID_TOKEN, "Malformed upload token.", "Call compile_sketch first.") from exc

    if time.time() > expiry:
        raise ToolError(errors.INVALID_TOKEN, "Upload token has expired.", "Recompile to get a fresh token.")

    expected = hmac.new(_SECRET, _token_payload(sketch_dir, fqbn, serial, expiry), sha256).digest()
    provided = base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
    if not hmac.compare_digest(expected, provided):
        raise ToolError(
            errors.INVALID_TOKEN,
            "Upload token does not match this sketch, board, and USB serial.",
            "Call compile_sketch for exactly this sketch_dir, fqbn, and connected board.",
        )


def resolve_sketch_dir(sketch_dir: str, roots: tuple[str, ...] | None = None) -> str:
    path = Path(sketch_dir).expanduser().resolve()
    if not path.is_dir():
        raise ToolError(errors.TOOL_MISSING, f"{path} is not a directory.")
    allowed = roots if roots is not None else ()
    if not allowed:
        raise ToolError(
            errors.SKETCH_NOT_ALLOWED,
            "No sketch directories are configured.",
            "Add [flash] sketch_roots in ~/.config/omarchy-hardware/config.toml.",
        )
    for root in allowed:
        root_path = Path(root).expanduser().resolve()
        try:
            path.relative_to(root_path)
            return str(path)
        except ValueError:
            continue
    raise ToolError(
        errors.SKETCH_NOT_ALLOWED,
        f"{path} is outside the configured sketch_roots.",
        "Move the sketch under a listed root, or add the directory to [flash] sketch_roots.",
    )


def list_fqbns(filter_text: str | None = None) -> list[dict[str, Any]]:
    result = _arduino_cli(["board", "listall", "--format", "json"], timeout=60)
    if result.returncode != 0:
        raise ToolError(errors.TOOL_MISSING, f"arduino-cli board listall failed: {_tail(result.stderr, 5)}")

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return []

    boards = payload.get("boards") or []
    entries = [{"name": b.get("name"), "fqbn": b.get("fqbn")} for b in boards if b.get("fqbn")]

    if filter_text:
        needle = filter_text.lower()
        entries = [e for e in entries if needle in (e["name"] or "").lower() or needle in e["fqbn"].lower()]
    return entries


def compile_sketch(
    sketch_dir: str,
    fqbn: str,
    *,
    serial: str = "",
    roots: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    resolved = resolve_sketch_dir(sketch_dir, roots)
    result = _arduino_cli(["compile", "--fqbn", fqbn, "--format", "json", resolved])

    if result.returncode != 0:
        return {
            "ok": False,
            "error": {
                "code": errors.SERIAL_ERROR,
                "message": "Compilation failed.",
                "hint": _tail(result.stderr or result.stdout, 30),
            },
        }

    build_path = None
    try:
        payload = json.loads(result.stdout or "{}")
        build_path = (payload.get("builder_result") or {}).get("build_path")
    except json.JSONDecodeError:
        pass

    return {
        "ok": True,
        "sketch_dir": resolved,
        "fqbn": fqbn,
        "build_path": build_path,
        "upload_token": mint_token(resolved, fqbn, serial),
        "usb_serial": serial or None,
        "expires_in_seconds": TOKEN_TTL_SECONDS,
        "stdout_tail": _tail(result.stdout, 10),
    }


def _append_upload_log(record: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    path = STATE_DIR / "flash.log"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        os.chmod(path, 0o600)
        handle.write(json.dumps({"at": time.time(), **record}) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _prepare_upload_log(record: dict[str, Any]) -> None:
    try:
        _append_upload_log({"event": "upload_started", **record})
    except OSError as exc:
        raise ToolError(
            errors.AUDIT_LOG_FAILED,
            "Refusing to upload because the flash audit log is unavailable.",
            f"Fix permissions for {STATE_DIR / 'flash.log'} and try again.",
        ) from exc


def upload_sketch(
    sketch_dir: str,
    port: str,
    fqbn: str,
    token: str,
    *,
    serial: str = "",
    roots: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    resolved = resolve_sketch_dir(sketch_dir, roots)
    verify_token(token, resolved, fqbn, serial)
    record = {"sketch_dir": resolved, "port": port, "fqbn": fqbn, "usb_serial": serial or None}
    _prepare_upload_log(record)

    started = time.monotonic()
    result = _arduino_cli(["upload", "-p", port, "--fqbn", fqbn, resolved])
    duration_ms = round((time.monotonic() - started) * 1000)

    try:
        _append_upload_log({"event": "upload_finished", **record, "returncode": result.returncode})
    except OSError as exc:
        raise ToolError(
            errors.AUDIT_LOG_FAILED,
            "Upload finished, but its result could not be written to the flash audit log.",
            f"Restore write access to {STATE_DIR / 'flash.log'} immediately.",
        ) from exc

    if result.returncode != 0:
        return {
            "ok": False,
            "error": {
                "code": errors.SERIAL_ERROR,
                "message": "Upload failed.",
                "hint": _tail(result.stderr or result.stdout, 30),
            },
        }

    return {"ok": True, "port": port, "fqbn": fqbn, "duration_ms": duration_ms, "output_tail": _tail(result.stdout, 10)}
