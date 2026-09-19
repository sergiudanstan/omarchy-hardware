"""Back up and restore the whole flash of an ESP32, with esptool.

Before a probe or an experiment overwrites a board, its current firmware can be
saved and put back later. ESP32 boards only: the ROM bootloader can read its own
flash, esptool ships with the Arduino ESP32 core, and the ROM cannot be
overwritten, so a failed restore can always be retried.

Identity comes from the chip's factory MAC address, read by esptool, not from
the USB bridge: CP210x bridges often all report serial "0001". A restore writes
only when the MAC of the connected chip equals the backup's.

Backups live next to the board journal, 0600, the last KEEP per board. Every call
is a fixed argv: esptool's absolute path, a validated port and fixed verbs.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from . import errors, journal
from .errors import ToolError

KEEP = 3
BAUD = "460800"
READ_TIMEOUT = 600
# esptool 5 prints "MAC:" padded to 20 columns: 6 bytes, or an 8-byte EUI-64 on C6/H2
# followed by a "BASE MAC:" line. Anchored so the base line is never taken first.
MAC = re.compile(r"^MAC:\s*([0-9a-f]{2}(?::[0-9a-f]{2}){5}(?::[0-9a-f]{2}){0,2})\s*$", re.IGNORECASE | re.MULTILINE)
CHIP = re.compile(r"(?:Chip is|Chip type:)\s*([A-Za-z0-9-]+)")
BACKUP_ID = re.compile(r"\d{8}-\d{6}-[0-9a-f]{12}")


def esptool_path() -> str:
    configured = os.environ.get("OMARCHY_HARDWARE_ESPTOOL")
    if configured:
        if not os.path.isabs(configured):
            raise ToolError(errors.CONFIG_ERROR, "OMARCHY_HARDWARE_ESPTOOL must be an absolute path.")
        candidates = [Path(configured)]
    else:
        root = Path(os.environ.get("OMARCHY_HARDWARE_ARDUINO_DATA") or Path.home() / ".arduino15")
        candidates = sorted(root.glob("packages/esp32/tools/esptool_py/*/esptool"),
                            key=lambda path: path.stat().st_mtime, reverse=True)
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    raise ToolError(errors.TOOL_MISSING, "esptool was not found.",
                    "Install the ESP32 core with arduino-cli (it ships esptool), or set OMARCHY_HARDWARE_ESPTOOL.")


def is_esp(board: dict[str, Any], fingerprinted: bool) -> bool:
    fqbn = board.get("suggested_fqbn") or ""
    return fingerprinted or fqbn.startswith("esp32:") or "esp32" in (board.get("board_type") or "")


def _esptool(port: str, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    try:
        # S603: argv list, shell=False: esptool's absolute path, a port that passed
        # policy.resolve_port, fixed verbs, and files this module named.
        return subprocess.run(  # noqa: S603
            [esptool_path(), "--port", port, "--baud", BAUD, *args],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError(errors.SERIAL_ERROR, "esptool timed out.", "Hold BOOT while it connects, then retry.") from exc


def _failed(result: subprocess.CompletedProcess, what: str) -> ToolError:
    tail = (result.stderr or result.stdout or "").strip().splitlines()[-3:]
    return ToolError(errors.SERIAL_ERROR, f"esptool could not {what}.", " ".join(tail)[:300])


def identify(port: str) -> dict[str, str]:
    result = _esptool(port, "read-mac")
    mac, chip = MAC.search(result.stdout or ""), CHIP.search(result.stdout or "")
    if result.returncode != 0 or mac is None:
        raise _failed(result, "read the chip's MAC address")
    return {"mac": mac.group(1).lower(), "chip": chip.group(1) if chip else "ESP32"}


def _dir(board: dict[str, Any]) -> Path:
    key = journal.board_key(board)
    return journal.journal_dir() / f"{key or 'no-serial'}.backups"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def create(board: dict[str, Any], port: str) -> dict[str, Any]:
    chip = identify(port)
    directory = _dir(board)
    directory.parent.mkdir(parents=True, exist_ok=True)
    directory.mkdir(mode=0o700, exist_ok=True)
    temp = directory / f".reading-{os.getpid()}.bin"
    result = _esptool(port, "read-flash", "--no-progress", "0", "ALL", str(temp), timeout=READ_TIMEOUT)
    if result.returncode != 0 or not temp.is_file():
        temp.unlink(missing_ok=True)
        raise _failed(result, "read the flash")
    os.chmod(temp, 0o600)
    digest = _sha256(temp)
    backup_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{digest[:12]}"
    target = directory / f"{backup_id}.bin"
    os.replace(temp, target)
    meta = {"backup_id": backup_id, "mac": chip["mac"], "chip": chip["chip"], "bytes": target.stat().st_size,
            "sha256": digest, "created": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    meta_path = directory / f"{backup_id}.json"
    meta_path.write_text(json.dumps(meta, indent=1))
    os.chmod(meta_path, 0o600)
    for old in list_backups(board)[KEEP:]:
        (directory / f"{old['backup_id']}.bin").unlink(missing_ok=True)
        (directory / f"{old['backup_id']}.json").unlink(missing_ok=True)
    return meta


def list_backups(board: dict[str, Any]) -> list[dict[str, Any]]:
    directory = _dir(board)
    backups = []
    for meta_path in directory.glob("*.json") if directory.is_dir() else []:
        try:
            meta = json.loads(meta_path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(meta, dict) and BACKUP_ID.fullmatch(str(meta.get("backup_id", ""))):
            backups.append(meta)
    return sorted(backups, key=lambda meta: meta["backup_id"], reverse=True)


def load(board: dict[str, Any], backup_id: str) -> tuple[dict[str, Any], Path]:
    if not isinstance(backup_id, str) or not BACKUP_ID.fullmatch(backup_id):
        raise ToolError(errors.INVALID_ARGUMENT, f"{backup_id!r} is not a backup id.", "Call firmware_backups.")
    meta = next((m for m in list_backups(board) if m["backup_id"] == backup_id), None)
    image = _dir(board) / f"{backup_id}.bin"
    if meta is None or not image.is_file() or image.is_symlink():
        raise ToolError(errors.ARTIFACT_INVALID, f"Backup {backup_id} is not stored for this board.",
                        "Call firmware_backups for this port.")
    if _sha256(image) != meta["sha256"]:
        raise ToolError(errors.ARTIFACT_INVALID, f"Backup {backup_id} no longer matches its checksum.")
    return meta, image


def restore(port: str, meta: dict[str, Any], image: Path) -> dict[str, Any]:
    chip = identify(port)
    if chip["mac"] != meta["mac"]:
        raise ToolError(
            errors.BOARD_MISMATCH,
            f"The chip on {port} is {chip['mac']}, but backup {meta['backup_id']} came from {meta['mac']}.",
            "A backup is only written back to the chip it was read from.",
        )
    result = _esptool(port, "write-flash", "0x0", str(image), timeout=READ_TIMEOUT)
    if result.returncode != 0:
        raise _failed(result, "write the flash")
    return {"backup_id": meta["backup_id"], "mac": chip["mac"], "bytes": meta["bytes"]}
