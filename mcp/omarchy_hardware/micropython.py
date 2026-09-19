"""Run code on a MicroPython board through its raw REPL, over an open serial session.

The raw REPL is MicroPython's machine interface:

    Ctrl-C Ctrl-C  interrupt the running program
    Ctrl-A         enter raw mode; the board answers "raw REPL; CTRL-B to exit" and ">"
    <code> Ctrl-D  run it; the board answers "OK", stdout, Ctrl-D, stderr, Ctrl-D, ">"
    Ctrl-B         back to the normal REPL

No mpremote dependency: the bytes go through the existing SerialSession, so
writes keep the port checks, byte budget and audit of serial_write. The file
helpers run fixed code templates, and paths and contents are embedded as JSON or
base64 literals, never spliced in as source text.

Code runs on the board, not on this machine. It can still drive whatever the
board is wired to, which is why running and writing need confirm=true.
"""

from __future__ import annotations

import base64
import json
import re
import time
from typing import Any

from . import errors
from .errors import ToolError

RAW_BANNER = "raw REPL; CTRL-B to exit\r\n>"
CHUNK = 256
CHUNK_PAUSE = 0.01
MAX_EXEC_MS = 30_000
MAX_FILE_BYTES = 32 * 1024
# Bytes of file content per program sent to the board: small boards compile each
# program in RAM, so a large file goes over in several.
PUT_CHUNK = 3 * 1024
MAX_OUTPUT = 16 * 1024
PATH = re.compile(r"/?[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*")


def check_path(path: str) -> str:
    if path == "/":
        return path
    if not isinstance(path, str) or len(path) > 128 or not PATH.fullmatch(path) or ".." in path.split("/"):
        raise ToolError(errors.INVALID_ARGUMENT, f"{path!r} is not a plain device path like /main.py or lib/x.py.")
    return path


def _read_until(session: Any, terminator: str, deadline: float) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    total = 0
    while True:
        remaining = int((deadline - time.monotonic()) * 1000)
        if remaining <= 0:
            return b"".join(chunks), False
        result = session.read(MAX_OUTPUT, min(remaining, 10_000), terminator)
        chunks.append(result["data"])
        total += len(result["data"])
        if not result["timed_out"] and b"".join(chunks).endswith(terminator.encode()):
            return b"".join(chunks), True
        if total > MAX_OUTPUT * 4:
            return b"".join(chunks), False


def _enter_raw(session: Any) -> None:
    session.write(b"\r\x03\x03")
    time.sleep(0.1)
    session.clear()
    session.write(b"\r\x01")
    _, found = _read_until(session, RAW_BANNER, time.monotonic() + 3)
    if not found:
        raise ToolError(
            errors.SERIAL_ERROR,
            "The board did not enter MicroPython's raw REPL.",
            "Check that it runs MicroPython (fingerprint_board shows the banner) and that the baud is 115200.",
        )


def run(session: Any, code: str, timeout_ms: int) -> dict[str, Any]:
    """Execute code in raw REPL mode and return stdout and stderr. Leaves the normal REPL."""
    source = code.encode("utf-8")
    deadline = time.monotonic() + max(100, min(int(timeout_ms), MAX_EXEC_MS)) / 1000
    with session.transaction():
        try:
            _enter_raw(session)
            for start in range(0, len(source), CHUNK):
                session.write(source[start:start + CHUNK])
                time.sleep(CHUNK_PAUSE)
            session.write(b"\x04")
            ack, found = _read_until(session, "OK", time.monotonic() + 3)
            if not found:
                raise ToolError(errors.SERIAL_ERROR, "The board did not accept the code.",
                                ack.decode("utf-8", errors="replace")[-300:])
            stdout, done_out = _read_until(session, "\x04", deadline)
            stderr, done_err = _read_until(session, "\x04", deadline) if done_out else (b"", False)
            finished = done_out and done_err
            if not finished:
                # Stop code that ran past the deadline, and let its KeyboardInterrupt
                # report drain here rather than into the next read.
                session.write(b"\x03")
                _read_until(session, ">", time.monotonic() + 2)
        finally:
            try:
                session.write(b"\x02")
                # Consume the normal REPL's banner and prompt for the same reason.
                _read_until(session, ">>> ", time.monotonic() + 1)
            except ToolError:
                # The session failed while leaving raw mode; the error that brought
                # us here, if any, is the one worth reporting.
                left_raw_mode = False
            else:
                left_raw_mode = True
    return {
        "finished": finished,
        "left_raw_mode": left_raw_mode,
        "stdout": stdout.removesuffix(b"\x04").decode("utf-8", errors="replace")[:MAX_OUTPUT],
        "stderr": stderr.removesuffix(b"\x04").decode("utf-8", errors="replace")[:MAX_OUTPUT],
        "untrusted": True,
    }


def list_code(path: str) -> str:
    target = json.dumps(check_path(path))
    return (
        "import os, json\n"
        f"p = {target}\n"
        "out = []\n"
        "for e in os.ilistdir(p):\n"
        "    out.append([e[0], 'dir' if e[1] == 0x4000 else 'file', e[3] if len(e) > 3 else None])\n"
        "print(json.dumps(out))\n"
    )


def put_programs(path: str, content: bytes) -> list[str]:
    """Programs that write content to path, one chunk each; each prints the file's new size."""
    if len(content) > MAX_FILE_BYTES:
        raise ToolError(errors.WRITE_TOO_LARGE, f"The file is {len(content)} bytes; the limit is {MAX_FILE_BYTES}.")
    target = json.dumps(check_path(path))
    chunks = [content[start:start + PUT_CHUNK] for start in range(0, len(content), PUT_CHUNK)] or [b""]
    programs = []
    for index, chunk in enumerate(chunks):
        encoded = base64.b64encode(chunk).decode()
        mode = "wb" if index == 0 else "ab"
        programs.append(
            "import ubinascii, os\n"
            f"f = open({target}, '{mode}')\n"
            f"f.write(ubinascii.a2b_base64('{encoded}'))\n"
            "f.close()\n"
            f"print(os.stat({target})[6])\n"
        )
    return programs


def parse_listing(stdout: str) -> list[dict[str, Any]]:
    try:
        entries = json.loads(stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise ToolError(errors.SERIAL_ERROR, "The board's file listing was unreadable.", stdout[-200:]) from exc
    if not isinstance(entries, list):
        raise ToolError(errors.SERIAL_ERROR, "The board's file listing was unreadable.")
    listing = []
    for entry in entries[:500]:
        if isinstance(entry, list) and len(entry) == 3 and isinstance(entry[0], str):
            listing.append({"name": entry[0][:128], "type": "dir" if entry[1] == "dir" else "file",
                            "size": entry[2] if isinstance(entry[2], int) else None})
    return listing
