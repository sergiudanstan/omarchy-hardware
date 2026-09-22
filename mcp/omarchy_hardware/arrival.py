"""The notification shown when a board is plugged in, and what its buttons do.

The bar widget runs `bin/board-arrived.sh /dev/ttyACM0` for a board it has not
seen in the last minute. This posts a notification naming the board, the user's
label for it and what was last flashed. It then waits for a button:

- "Start with Claude" runs project.launch(), which opens a briefed session;
- "Serial monitor" opens arduino-cli's monitor in a terminal, when it is installed.

The widget passes only a port matching /dev/tty(ACM|USB)N, and it is checked
again here. Product strings come from the device, and the notification server
renders body markup, so they are escaped.
"""

from __future__ import annotations

import html
import os
import subprocess
import sys
from datetime import UTC, datetime
from typing import Any

from . import board_profiles, journal, project
from .boards import enumerate_boards
from .errors import ToolError

NOTIFY_SEND = "/usr/bin/notify-send"
ARDUINO_CLI = os.environ.get("OMARCHY_HARDWARE_ARDUINO_CLI", "/usr/local/bin/arduino-cli")
EXPIRE_MS = 60_000
WAIT_TIMEOUT_S = 90


def describe(port: str) -> dict[str, Any] | None:
    for board in enumerate_boards():
        if board["port"] == port:
            try:
                history = journal.history(board)
            except ToolError:
                history = {"tracked": False}
            return {
                "board": board,
                "history": history,
                "profile_id": board_profiles.profile_id_for_fqbn(board.get("suggested_fqbn")),
            }
    return None


def _ago(timestamp: str, now: datetime | None = None) -> str:
    try:
        then = datetime.fromisoformat(timestamp)
    except (TypeError, ValueError):
        return "at an unknown time"
    seconds = max(0, int(((now or datetime.now(UTC)) - then).total_seconds()))
    for unit, size in (("day", 86_400), ("hour", 3_600), ("minute", 60)):
        if seconds >= size:
            count = seconds // size
            return f"{count} {unit}{'' if count == 1 else 's'} ago"
    return "just now"


def _text(value: object, limit: int = 48) -> str:
    cleaned = " ".join("".join(ch if ch.isprintable() else " " for ch in str(value or "")).split())
    return html.escape(cleaned[:limit], quote=False)


def message(info: dict[str, Any], now: datetime | None = None) -> tuple[str, str]:
    board = info["board"]
    history = info["history"]
    name = _text(board.get("friendly_name") or "Serial device")
    label = history.get("label")
    summary = f"{_text(label, 40)} ({name})" if label else name

    lines = [f"Plugged in on {_text(board['port'].removeprefix('/dev/'), 16)}."]
    last = history.get("last_upload")
    if last:
        lines.append(f"Last flashed {_ago(last.get('at'), now)} with {_text(last.get('sketch'), 40)}.")
    elif history.get("tracked") and not history.get("known"):
        lines.append("New board, no history yet.")
    if not info["profile_id"]:
        lines.append("No pin profile for this board.")
    return summary, "\n".join(lines)


def actions() -> list[tuple[str, str]]:
    available = [("claude", "Start with Claude")]
    # A relative override would resolve against whatever directory the bar runs
    # this from; flash.py refuses it outright, so the monitor button does too.
    if os.path.isabs(ARDUINO_CLI) and os.path.isfile(ARDUINO_CLI) and os.access(ARDUINO_CLI, os.X_OK):
        available.append(("monitor", "Serial monitor"))
    return available


def notify(summary: str, body: str, choices: list[tuple[str, str]]) -> str:
    """Show the notification and return the chosen action id, or "" if dismissed."""
    argv = [NOTIFY_SEND, "--app-name=Hardware", "--icon=drive-removable-media", f"--expire-time={EXPIRE_MS}", "--wait"]
    argv += [f"--action={key}={label}" for key, label in choices]
    try:
        # S603: argv list, shell=False. summary and body are escaped and are passed as
        # separate arguments after the options, so they cannot become options.
        result = subprocess.run(  # noqa: S603
            [*argv, "--", summary, body], capture_output=True, text=True, timeout=WAIT_TIMEOUT_S, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    choice = result.stdout.strip()
    return choice if choice in {key for key, _ in choices} else ""


def open_monitor(port: str, baud: int) -> None:
    if not os.path.isabs(ARDUINO_CLI):
        return
    command = [ARDUINO_CLI, "monitor", "-p", port, "--config", f"baudrate={int(baud)}"]
    project.spawn(project.terminal_argv(project.projects_root(), command))


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m omarchy_hardware.arrival /dev/ttyACM0", file=sys.stderr)
        return 2
    try:
        port = project.check_port(args[0])
    except project.LaunchError as exc:
        print(exc, file=sys.stderr)
        return 2
    info = describe(port)
    if info is None:
        return 0  # Gone again before we looked: nothing to announce.

    summary, body = message(info)
    choice = notify(summary, body, actions())
    if choice == "claude":
        return project.main([port])
    if choice == "monitor":
        try:
            project.projects_root().mkdir(parents=True, exist_ok=True)
            open_monitor(port, info["board"].get("suggested_baud") or 115200)
        except (OSError, project.LaunchError) as exc:
            project.notify_error(str(exc))
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
