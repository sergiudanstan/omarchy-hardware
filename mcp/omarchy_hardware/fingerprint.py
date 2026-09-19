"""Identify a board from what it prints when its port is opened.

USB alone cannot name a board behind a generic bridge chip (CH340, CP210x): an
ESP32 DevKit and a USB-serial dongle look the same. Opening the port pulses DTR
and RTS, which resets most boards, and the ESP32 boot ROM then prints a banner
that names the chip. MicroPython and CircuitPython name themselves too, and
firmware built through /hw-build prints a {"fw": ...} line.

Matching is by literal substrings, never regular expressions: the text comes
from the device. A match is evidence, not proof. A device can print anything,
so flashing an unidentified adapter on the strength of a fingerprint stays off
unless `[flash] allow_fingerprinted = true`.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any

LISTEN_MS = 3_000
FINGERPRINT_BAUD = 115200
MAX_CAPTURE = 16 * 1024
SAMPLE_LINES = 12
SAMPLE_CHARS = 160
# A fingerprint vouches for the board on that port for this long.
CACHE_SECONDS = 300


@dataclass(frozen=True)
class RomSignature:
    marker: str
    chip: str
    fqbns: tuple[str, ...]
    profile_id: str | None = None


# The first FQBN is the suggestion; all of them are accepted for the upload gate.
ROM_SIGNATURES = (
    RomSignature("ESP-ROM:esp32s3", "ESP32-S3", ("esp32:esp32:esp32s3",)),
    RomSignature("ESP-ROM:esp32s2", "ESP32-S2", ("esp32:esp32:esp32s2",)),
    RomSignature("ESP-ROM:esp32c3", "ESP32-C3", ("esp32:esp32:esp32c3",)),
    RomSignature("ESP-ROM:esp32c6", "ESP32-C6", ("esp32:esp32:esp32c6",)),
    RomSignature(
        "ets Jun  8 2016",
        "ESP32",
        ("esp32:esp32:esp32", "esp32:esp32:esp32doit-devkit-v1"),
        "esp32_devkitc",
    ),
)


def _runtime(line: str, marker: str, kind: str) -> dict[str, Any] | None:
    """Parse "MicroPython v1.22.2 on 2024-02-22; Raspberry Pi Pico with RP2040"."""
    start = line.find(marker)
    if start == -1:
        return None
    text = line[start:]
    version = text[len(marker):].split(" ", 1)[0].strip()
    board = mcu = None
    if "; " in text:
        tail = text.split("; ", 1)[1]
        board, _, mcu = tail.partition(" with ")
        board, mcu = board.strip() or None, mcu.strip() or None
    return {"kind": kind, "version": version[:40], "board": (board or "")[:80] or None, "mcu": (mcu or "")[:40] or None}


def _firmware(line: str) -> dict[str, Any] | None:
    text = line.strip()
    if not text.startswith("{") or len(text) > 512:
        return None
    try:
        parsed = json.loads(text)
    except (ValueError, RecursionError):
        return None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("fw"), str):
        return None
    build = parsed.get("build")
    return {"kind": "omarchy_firmware", "fw": parsed["fw"][:60], "build": str(build)[:60] if build else None}


def analyse(data: bytes) -> dict[str, Any]:
    """Pure: turn captured bytes into matches and a bounded sample."""
    text = data.decode("utf-8", errors="replace")
    lines = [line.strip("\r") for line in text.split("\n")]
    matches: list[dict[str, Any]] = []
    seen: set[str] = set()

    for signature in ROM_SIGNATURES:
        if signature.marker in text and signature.chip not in seen:
            seen.add(signature.chip)
            matches.append(
                {
                    "kind": "esp_rom",
                    "chip": signature.chip,
                    "suggested_fqbn": signature.fqbns[0],
                    "accepted_fqbns": list(signature.fqbns),
                    "profile_id": signature.profile_id,
                    "evidence": signature.marker,
                }
            )

    for line in lines:
        for found in (
            _runtime(line, "MicroPython v", "micropython"),
            _runtime(line, "Adafruit CircuitPython ", "circuitpython"),
            _firmware(line),
        ):
            if found and found["kind"] not in seen:
                seen.add(found["kind"])
                matches.append(found)

    printable = ["".join(ch for ch in line if ch.isprintable())[:SAMPLE_CHARS] for line in lines]
    sample = [line for line in printable if line.strip()][-SAMPLE_LINES:]
    return {"matches": matches, "bytes_read": len(data), "sample": sample}


class Cache:
    """Recent fingerprints by (port, USB serial), for the upload gate."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(board: dict[str, Any]) -> tuple[str, str, str]:
        return (board["port"], f"{board.get('vid')}:{board.get('pid')}", (board.get("serial") or "").strip())

    def store(self, board: dict[str, Any], result: dict[str, Any]) -> None:
        with self._lock:
            self._entries[self._key(board)] = (time.monotonic(), result)

    def accepts(self, board: dict[str, Any], fqbn: str) -> dict[str, Any] | None:
        """The ROM match that vouches for flashing `fqbn` to this board, if any."""
        with self._lock:
            entry = self._entries.get(self._key(board))
        if entry is None or time.monotonic() - entry[0] > CACHE_SECONDS:
            return None
        for match in entry[1]["matches"]:
            if match["kind"] == "esp_rom" and fqbn in match["accepted_fqbns"]:
                return match
        return None


def capture(session: Any, listen_ms: int | None = None) -> bytes:
    """Collect what the device sends for listen_ms after the port was opened."""
    deadline = time.monotonic() + (LISTEN_MS if listen_ms is None else listen_ms) / 1000.0
    chunks: list[bytes] = []
    total = 0
    while total < MAX_CAPTURE:
        remaining_ms = int((deadline - time.monotonic()) * 1000)
        if remaining_ms <= 0:
            break
        result = session.read(MAX_CAPTURE - total, remaining_ms, None)
        chunks.append(result["data"])
        total += len(result["data"])
    return b"".join(chunks)
