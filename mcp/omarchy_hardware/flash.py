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
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import file_digest, sha256
from pathlib import Path
from typing import Any

from . import errors
from .config import STATE_DIR
from .errors import ToolError

TOKEN_TTL_SECONDS = 300
_SECRET = secrets.token_bytes(32)
ARDUINO_CLI = os.environ.get("OMARCHY_HARDWARE_ARDUINO_CLI", "/usr/local/bin/arduino-cli")

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


def _token_payload(
    sketch_dir: str, fqbn: str, serial: str, expiry: int, artifact_path: str = "", artifact_digest: str = ""
) -> bytes:
    return json.dumps(
        [sketch_dir, fqbn, serial, expiry, artifact_path, artifact_digest],
        separators=(",", ":"),
    ).encode()


def mint_token(
    sketch_dir: str, fqbn: str, serial: str = "", artifact_path: str = "", artifact_digest: str = ""
) -> str:
    expiry = int(time.time()) + TOKEN_TTL_SECONDS
    digest = hmac.new(
        _SECRET,
        _token_payload(sketch_dir, fqbn, serial, expiry, artifact_path, artifact_digest),
        sha256,
    ).digest()
    return f"{base64.urlsafe_b64encode(digest).decode().rstrip('=')}.{expiry}"


def verify_token(
    token: str,
    sketch_dir: str,
    fqbn: str,
    serial: str = "",
    artifact_path: str = "",
    artifact_digest: str = "",
) -> None:
    try:
        signature, expiry_text = token.rsplit(".", 1)
        expiry = int(expiry_text)
    except (ValueError, AttributeError) as exc:
        raise ToolError(errors.INVALID_TOKEN, "Malformed upload token.", "Call compile_sketch first.") from exc

    if time.time() > expiry:
        raise ToolError(errors.INVALID_TOKEN, "Upload token has expired.", "Recompile to get a fresh token.")

    expected = hmac.new(
        _SECRET, _token_payload(sketch_dir, fqbn, serial, expiry, artifact_path, artifact_digest), sha256
    ).digest()
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


def _resolve_artifact_dir(path: str) -> str:
    """Resolve an artifact directory without following a final-path symlink escape."""
    if not isinstance(path, str) or not path or "\x00" in path:
        raise ToolError(errors.ARTIFACT_INVALID, "The compile artifact is unavailable.", "Recompile the sketch.")
    root = Path(path).expanduser()
    if root.is_symlink():
        raise ToolError(
            errors.ARTIFACT_INVALID,
            "The compile artifact path must not be a symlink.",
            "Recompile the sketch.",
        )
    resolved = root.resolve()
    if not resolved.is_dir():
        raise ToolError(errors.ARTIFACT_INVALID, "The compile artifact is unavailable.", "Recompile the sketch.")
    return str(resolved)


def _artifact_digest(path: str) -> str:
    """Hash artifact file contents without following symlinks out of the tree."""
    root = Path(_resolve_artifact_dir(path))
    digest = sha256()
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        if any(Path(dirpath, name).is_symlink() for name in dirnames):
            raise ToolError(errors.ARTIFACT_INVALID, "The compile artifact contains a symlink.")
        for name in filenames:
            candidate = Path(dirpath, name)
            if candidate.is_symlink():
                raise ToolError(
                    errors.ARTIFACT_INVALID,
                    "The compile artifact contains a symlink and cannot be trusted.",
                    "Recompile the sketch.",
                )
            if candidate.is_file():
                files.append(candidate)
    files.sort()
    if not files:
        raise ToolError(errors.ARTIFACT_INVALID, "The compile artifact is empty.", "Recompile the sketch.")
    for file in files:
        try:
            relative = file.relative_to(root).as_posix().encode()
            digest.update(len(relative).to_bytes(8, "big"))
            digest.update(relative)
            # Fixed-length content hashes keep file boundaries unambiguous.
            with file.open("rb") as handle:
                digest.update(file_digest(handle, "sha256").digest())
        except OSError as exc:
            raise ToolError(
                errors.ARTIFACT_INVALID,
                "The compile artifact cannot be read.",
                "Recompile the sketch.",
            ) from exc
    return digest.hexdigest()


@contextmanager
def _artifact_snapshot(path: str, expected_digest: str) -> Iterator[str]:
    """Verify the private copy that the uploader will read, not a mutable build cache."""
    with tempfile.TemporaryDirectory(prefix="omarchy-hardware-upload-") as temporary:
        snapshot = str(Path(temporary) / "build")
        try:
            # Preserve links so the digest rejects them rather than copying their targets.
            shutil.copytree(path, snapshot, symlinks=True)
        except (OSError, shutil.Error) as exc:
            raise ToolError(
                errors.ARTIFACT_INVALID, "The compile artifact cannot be copied.", "Recompile the sketch."
            ) from exc
        if _artifact_digest(snapshot) != expected_digest:
            raise ToolError(
                errors.ARTIFACT_INVALID, "The compile artifact changed since compilation.", "Recompile the sketch."
            )
        yield snapshot


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
    except json.JSONDecodeError as exc:
        raise ToolError(
            errors.ARTIFACT_INVALID,
            "arduino-cli returned unreadable compile output.",
            "Recompile the sketch.",
        ) from exc

    if not isinstance(build_path, str) or not build_path.strip():
        raise ToolError(
            errors.ARTIFACT_INVALID,
            "arduino-cli did not report a build artifact path.",
            "Upgrade arduino-cli or recompile with JSON output enabled.",
        )

    resolved_artifact = _resolve_artifact_dir(build_path)
    digest = _artifact_digest(resolved_artifact)

    return {
        "ok": True,
        "sketch_dir": resolved,
        "fqbn": fqbn,
        "build_path": resolved_artifact,
        "artifact_path": resolved_artifact,
        "artifact_digest": digest,
        "upload_token": mint_token(resolved, fqbn, serial, resolved_artifact, digest),
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
    artifact_path: str = "",
    artifact_digest: str = "",
    roots: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    resolved = resolve_sketch_dir(sketch_dir, roots)
    if not artifact_path or not artifact_digest:
        raise ToolError(
            errors.ARTIFACT_INVALID,
            "Upload requires the exact successful compile artifact.",
            "Recompile the sketch.",
        )
    resolved_artifact = _resolve_artifact_dir(artifact_path)
    verify_token(token, resolved, fqbn, serial, resolved_artifact, artifact_digest)
    record = {
        "sketch_dir": resolved,
        "port": port,
        "fqbn": fqbn,
        "usb_serial": serial or None,
        "artifact_digest": artifact_digest,
        "artifact_path": resolved_artifact,
    }
    with _artifact_snapshot(resolved_artifact, artifact_digest) as snapshot:
        _prepare_upload_log(record)
        started = time.monotonic()
        result = _arduino_cli(["upload", "-p", port, "--fqbn", fqbn, "--input-dir", snapshot])
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
