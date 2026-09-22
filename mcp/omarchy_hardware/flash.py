"""Compiling and uploading sketches via arduino-cli.

Uploading is the only genuinely destructive operation here, so it is gated twice:
the caller must present a token minted by a *successful compile in this process*,
and must pass confirm=True. That makes "flash the board" impossible to reach in a
single unconsidered tool call.
"""

from __future__ import annotations

import base64
import errno
import hmac
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from hashlib import file_digest, sha256
from pathlib import Path
from typing import Any

from . import audit, errors
from .errors import ToolError

TOKEN_TTL_SECONDS = 300
_SECRET = secrets.token_bytes(32)
ARDUINO_CLI = os.environ.get("OMARCHY_HARDWARE_ARDUINO_CLI", "/usr/local/bin/arduino-cli")

if not os.path.isabs(ARDUINO_CLI):
    raise RuntimeError("OMARCHY_HARDWARE_ARDUINO_CLI must be an absolute path")

# vendor:architecture:board, optionally followed by menu options
# (arduino:avr:nano:cpu=atmega328old). Validated before it reaches argv: relying
# on the argument parser inside arduino-cli to refuse a value that starts with
# "-" is depending on a third-party parser's behaviour for a control we own.
FQBN_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+(:[A-Za-z0-9_.=,-]+)?$")


def check_fqbn(fqbn: str) -> str:
    if not isinstance(fqbn, str) or not FQBN_PATTERN.match(fqbn):
        raise ToolError(
            errors.UNKNOWN_BOARD,
            f"{fqbn!r} is not a valid FQBN.",
            "Use vendor:architecture:board, for example arduino:avr:uno. Call list_fqbns to see them.",
        )
    return fqbn


def run_group(argv: list[str], timeout: float) -> subprocess.CompletedProcess:
    """subprocess.run, but a timeout kills the whole process group.

    arduino-cli and esptool hand the port to children (avrdude, picotool,
    esptool's own stub loader). Killing only the parent left them writing flash
    and holding the port after the tool had already reported a timeout.
    """
    # S603: callers pass argv lists with shell=False, never a shell string.
    with subprocess.Popen(  # noqa: S603
        argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass  # The group already exited between the timeout and the kill.
            process.communicate()
            raise
        return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


def _arduino_cli(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    try:
        # Every element of `args` is either a literal verb or a value already
        # validated upstream (the port by policy.resolve_port, the sketch dir by
        # resolve_sketch_dir).
        return run_group([ARDUINO_CLI, *args], timeout)
    except FileNotFoundError as exc:
        raise ToolError(
            errors.TOOL_MISSING,
            f"arduino-cli was not found at {ARDUINO_CLI}.",
            "Install a trusted, pinned arduino-cli release there, or set "
            "OMARCHY_HARDWARE_ARDUINO_CLI to its absolute path. setup.sh does not install it.",
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
    check_fqbn(fqbn)
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


def _flashed_elf(snapshot: str) -> str | None:
    """The sketch's ELF in the build directory.

    arduino-cli names it <sketch>.ino.elf at the top level. Cores may add others
    beside it (bootloaders, partition helpers), so the .ino.elf wins. Otherwise a
    single ELF anywhere in the build is taken, and anything ambiguous is refused
    rather than guessed.
    """
    root = Path(snapshot)

    def usable(paths: list[Path]) -> list[Path]:
        return [path for path in paths if path.is_file() and not path.is_symlink()]

    sketch = usable(sorted(root.glob("*.ino.elf")))
    if len(sketch) == 1:
        return str(sketch[0])
    everything = usable(sorted(root.rglob("*.elf")))
    return str(everything[0]) if len(everything) == 1 else None


def _uf2_from_snapshot(snapshot: str) -> str:
    root = Path(snapshot)

    def usable(paths: list[Path]) -> list[Path]:
        return [path for path in paths if path.is_file() and not path.is_symlink()]

    sketch = usable(sorted(root.glob("*.ino.uf2")))
    if len(sketch) == 1:
        return str(sketch[0])
    everything = usable(sorted(root.rglob("*.uf2")))
    if len(everything) == 1:
        return str(everything[0])
    raise ToolError(
        errors.ARTIFACT_INVALID,
        "The compile artifact has no single UF2 image.",
        "Compile with an RP2040/RP2350 FQBN so arduino-cli produces a .uf2.",
    )


def _prepare_upload_log(record: dict[str, Any]) -> None:
    audit.require("upload_started", "flash this board", **record)


# Errors a UF2 copy sees when the bootloader has already rebooted: the RP2 ROM
# drops its USB disk as soon as the last block arrives, before the kernel has
# written back the directory entry and FAT.
_UF2_GONE_ERRNOS = frozenset({errno.EIO, errno.ENODEV, errno.ENOENT, errno.ENXIO, errno.ESHUTDOWN})
UF2_GONE_WAIT_S = 3.0


def _volume_gone(volume: str, deadline_s: float) -> bool:
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        if not os.path.exists(os.path.join(volume, "INFO_UF2.TXT")):
            return True
        time.sleep(0.1)
    return not os.path.exists(os.path.join(volume, "INFO_UF2.TXT"))


def _copy_uf2(uf2: str, volume: str) -> tuple[str, str | None]:
    """Copy the image onto a BOOTSEL volume. Returns the destination and a note."""
    dest = os.path.join(volume, os.path.basename(uf2))
    size = os.path.getsize(uf2)
    written = 0
    try:
        # O_NOFOLLOW: a device posing as a Pico must not redirect the write
        # through a symlink it planted on its own volume.
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
        with open(uf2, "rb") as source, os.fdopen(fd, "wb") as target:
            shutil.copyfileobj(source, target)
            target.flush()
            written = size
            # close() only drains Python's buffer; Linux can still have
            # firmware queued for the USB volume. Surface writeback errors
            # before recording a completed upload or keeping its ELF.
            os.fsync(target.fileno())
    except OSError as exc:
        if written == size and exc.errno in _UF2_GONE_ERRNOS and _volume_gone(volume, UF2_GONE_WAIT_S):
            return dest, (
                "The board rebooted before the copy was acknowledged, which RP2 bootloaders do once the "
                "last block arrives. Check that it runs the new sketch."
            )
        raise ToolError(
            errors.SERIAL_ERROR,
            f"Could not copy the UF2 image to {volume}.",
            "The RPI-RP2 volume must stay mounted until the copy finishes.",
        ) from exc
    return dest, None


def _note_upload_failed(record: dict[str, Any], exc: BaseException) -> None:
    """Close the upload_started record for an upload that raised."""
    code = exc.code if isinstance(exc, ToolError) else type(exc).__name__
    try:
        audit.note("upload_finished", **record, returncode=None, error=code)
    except OSError as audit_exc:
        raise ToolError(
            errors.AUDIT_LOG_FAILED,
            f"Upload failed ({code}), and that could not be written to the audit log.",
            f"Restore write access to {audit.log_path()} immediately.",
        ) from audit_exc


def _keep_flashed_elf(
    result: subprocess.CompletedProcess, snapshot: str, keep_elf: Callable[[str], Any] | None
) -> tuple[bool, str | None]:
    if result.returncode != 0 or keep_elf is None:
        return False, None
    # The verified snapshot is what went to the board; the build cache may
    # already hold a newer build. Best effort: the upload has happened.
    elf = _flashed_elf(snapshot)
    if elf is None:
        return False, "The build has no single sketch ELF, so crashes from this upload cannot be decoded."
    try:
        kept = bool(keep_elf(elf))
    except OSError as exc:
        return False, f"The sketch ELF could not be kept ({type(exc).__name__}); crashes cannot be decoded."
    if not kept:
        return False, "The sketch ELF was not kept (the board has no USB serial or it is too large)."
    return True, None


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
    keep_elf: Callable[[str], Any] | None = None,
    before_write: Callable[[], None] | None = None,
) -> dict[str, Any]:
    resolved = resolve_sketch_dir(sketch_dir, roots)
    check_fqbn(fqbn)
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
    elf_kept, elf_note, uf2_note = False, None, None
    started_logged = False
    try:
        with _artifact_snapshot(resolved_artifact, artifact_digest) as snapshot:
            # A directory port is a Pico BOOTSEL volume: refuse a missing or ambiguous
            # UF2 before the caller's budget or the upload_started audit entry.
            uf2 = _uf2_from_snapshot(snapshot) if os.path.isdir(port) else None
            if before_write is not None:
                before_write()
            _prepare_upload_log(record)
            started_logged = True
            started = time.monotonic()
            if uf2 is not None:
                dest, uf2_note = _copy_uf2(uf2, port)
                result = subprocess.CompletedProcess(["uf2-copy", uf2, dest], returncode=0, stdout=dest, stderr="")
            else:
                result = _arduino_cli(["upload", "-p", port, "--fqbn", fqbn, "--input-dir", snapshot])
            elf_kept, elf_note = _keep_flashed_elf(result, snapshot, keep_elf)
    except Exception as exc:
        # Every upload_started gets its upload_finished, including a timeout or a
        # failed UF2 copy; an open-ended record reads like an upload in flight.
        if started_logged:
            _note_upload_failed(record, exc)
        raise
    duration_ms = round((time.monotonic() - started) * 1000)


    try:
        audit.note("upload_finished", **record, returncode=result.returncode)
    except OSError as exc:
        raise ToolError(
            errors.AUDIT_LOG_FAILED,
            "Upload finished, but its result could not be written to the audit log.",
            f"Restore write access to {audit.log_path()} immediately.",
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

    success = {
        "ok": True,
        "port": port,
        "fqbn": fqbn,
        "sketch_dir": resolved,
        "artifact_digest": artifact_digest,
        "duration_ms": duration_ms,
        "output_tail": _tail(result.stdout, 10),
    }
    if keep_elf is not None:
        success["elf_kept"] = elf_kept
        if elf_note:
            success["elf_note"] = elf_note
    if uf2_note:
        success["note"] = uf2_note
    return success
