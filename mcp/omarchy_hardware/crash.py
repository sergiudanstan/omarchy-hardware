"""Decode a firmware crash report into source lines.

ESP32 cores print a panic report on serial: a Xtensa "Backtrace:" of PC:SP pairs,
or RISC-V (C3/C6) "MEPC"/"RA" registers, plus "abort() was called at PC 0x...".
parse() pulls out only hexadecimal addresses from that text. decode() runs the
toolchain's addr2line, a fixed argv, against the ELF kept from the upload
(journal.store_elf), so the frames match what is actually on the board.

The crash text is device output relayed by the model. Nothing in it reaches a
command line except validated addresses.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from . import errors
from .errors import ToolError

MAX_TEXT = 8 * 1024
MAX_ADDRESSES = 32
ADDR2LINE_TIMEOUT = 20

_HEX = r"0x[0-9a-fA-F]{8}"
_BACKTRACE = re.compile(rf"Backtrace:((?:\s*{_HEX}:{_HEX})+)")
_PAIR = re.compile(rf"({_HEX}):{_HEX}")
_REGISTER = re.compile(rf"\b(PC|MEPC|RA|EXCVADDR)\s*:\s*({_HEX})")
_ABORT = re.compile(rf"abort\(\) was called at PC ({_HEX})")
_REASON = re.compile(r"(Guru Meditation Error: [^\r\n]{0,160}|abort\(\) was called[^\r\n]{0,80}|"
                     r"Stack smashing protect failure!|\*\*\*ERROR\*\*\* A stack overflow[^\r\n]{0,120}|"
                     r"assert failed:[^\r\n]{0,160})")

# ELF e_machine values and the addr2line binaries that understand them.
MACHINES = {
    94: ("xtensa", ("xtensa-esp-elf-addr2line", "xtensa-esp32-elf-addr2line", "xtensa-esp32s3-elf-addr2line",
                    "xtensa-esp32s2-elf-addr2line")),
    243: ("riscv", ("riscv32-esp-elf-addr2line", "riscv32-unknown-elf-addr2line")),
    40: ("arm", ("arm-none-eabi-addr2line",)),
    83: ("avr", ("avr-addr2line",)),
}


def parse(text: object) -> dict[str, Any] | None:
    """The crash kind, reason and code addresses in a report, or None if it is not one."""
    if not isinstance(text, str):
        raise ToolError(errors.INVALID_ARGUMENT, "crash_text must be the serial text of the crash report.")
    text = text[-MAX_TEXT:]
    addresses: list[str] = []
    kind = None

    for match in _BACKTRACE.finditer(text):
        kind = "esp_xtensa_backtrace"
        addresses += [pc for pc in _PAIR.findall(match.group(1))]
    registers = dict((name, value) for name, value in _REGISTER.findall(text))
    if "MEPC" in registers:
        kind = kind or "esp_riscv_panic"
        addresses = [registers["MEPC"], *([registers["RA"]] if "RA" in registers else []), *addresses]
    elif "PC" in registers:
        kind = kind or "esp_xtensa_panic"
        addresses = [registers["PC"], *addresses]
    abort = _ABORT.search(text)
    if abort:
        kind = kind or "esp_abort"
        addresses = [abort.group(1), *addresses]

    reason = _REASON.search(text)
    if kind is None and reason is None:
        return None
    unique: list[str] = []
    for address in addresses:
        address = address.lower()
        # 0x00000000 and repeated frames carry no information.
        if int(address, 16) and address not in unique:
            unique.append(address)
    return {
        "kind": kind or "reset_reason_only",
        "reason": reason.group(1).strip() if reason else None,
        "addresses": unique[:MAX_ADDRESSES],
    }


def elf_machine(path: Path) -> int | None:
    try:
        with open(path, "rb") as handle:
            header = handle.read(20)
    except OSError:
        return None
    if len(header) < 20 or header[:4] != b"\x7fELF":
        return None
    little = header[5] == 1
    return int.from_bytes(header[18:20], "little" if little else "big")


def arduino_data_dir() -> Path:
    configured = os.environ.get("OMARCHY_HARDWARE_ARDUINO_DATA")
    if configured and os.path.isabs(configured):
        return Path(configured)
    return Path.home() / ".arduino15"


def find_addr2line(machine: int) -> str | None:
    """The newest addr2line for this machine among the installed Arduino cores."""
    if machine not in MACHINES:
        return None
    packages = arduino_data_dir() / "packages"
    for name in MACHINES[machine][1]:
        found = sorted(packages.glob(f"*/tools/*/*/bin/{name}"), key=lambda path: path.stat().st_mtime, reverse=True)
        for path in found:
            if path.is_file() and os.access(path, os.X_OK):
                return str(path)
    return None


def decode(elf: Path, addresses: list[str]) -> list[dict[str, Any]]:
    machine = elf_machine(elf)
    if machine is None:
        raise ToolError(errors.ARTIFACT_INVALID, "The stored firmware is not an ELF file.", "Flash the sketch again.")
    tool = find_addr2line(machine)
    if tool is None:
        arch = MACHINES.get(machine, (f"machine {machine}",))[0]
        raise ToolError(
            errors.TOOL_MISSING,
            f"No addr2line for {arch} was found under {arduino_data_dir()}/packages.",
            "Install the board's core with arduino-cli, which ships the toolchain.",
        )
    for address in addresses:
        if not re.fullmatch(r"0x[0-9a-f]{8}", address):
            raise ToolError(errors.INVALID_ARGUMENT, f"{address!r} is not a code address.")
    # S603: argv list, shell=False: a toolchain binary found under the Arduino data
    # directory (the same binaries compile_sketch runs), the kept ELF, and addresses
    # that matched 0x followed by 8 hex digits.
    try:
        result = subprocess.run(  # noqa: S603
            [tool, "-a", "-f", "-i", "-C", "-e", str(elf), *addresses],
            capture_output=True, text=True, timeout=ADDR2LINE_TIMEOUT, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError(errors.SERVICE_ERROR, f"addr2line took longer than {ADDR2LINE_TIMEOUT} s.",
                        "Try again; if it keeps hanging, reinstall the board's core with arduino-cli.") from exc
    except OSError as exc:
        raise ToolError(errors.TOOL_MISSING, f"addr2line could not be run ({type(exc).__name__}).",
                        "Reinstall the board's core with arduino-cli, which ships the toolchain.") from exc
    if result.returncode != 0:
        raise ToolError(errors.SERVICE_ERROR, "addr2line failed.", result.stderr.strip()[:300])
    return _frames(addresses, result.stdout)


def _location(function: str, location: str) -> dict[str, Any]:
    file, _, line = location.rpartition(":")
    line = line.split(" ", 1)[0]  # "42 (discriminator 1)"
    return {
        "function": function if function != "??" else None,
        "file": file if file and file != "??" else None,
        "line": int(line) if line.isdigit() and int(line) > 0 else None,
    }


def _frames(addresses: list[str], output: str) -> list[dict[str, Any]]:
    """Parse `addr2line -a -f -i`: each address line, then function/location pairs.

    With -i an inlined call adds more pairs; the first is the innermost function,
    the rest are where it was inlined.
    """
    blocks: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in output.splitlines():
        text = line.strip()
        if re.fullmatch(r"0x[0-9a-fA-F]+", text):
            current = blocks.setdefault(f"0x{int(text, 16):08x}", [])
        elif current is not None:
            current.append(text)
    frames: list[dict[str, Any]] = []
    for address in addresses:
        lines = blocks.get(address, [])
        pairs = [_location(lines[i], lines[i + 1]) for i in range(0, len(lines) - 1, 2)]
        frame: dict[str, Any] = {"address": address, **(pairs[0] if pairs else _location("??", "??:0"))}
        if len(pairs) > 1:
            frame["inlined_into"] = pairs[1:]
        frames.append(frame)
    return frames
