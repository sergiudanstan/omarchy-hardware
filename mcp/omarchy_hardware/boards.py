"""Enumerate connected USB serial boards by walking sysfs.

Deliberately stdlib-only: the bar widget calls this before setup has run, when
pyserial may not be installed yet.
"""

from __future__ import annotations

import glob
import json
import os
import sys

from .ids import identify

TTY_GLOBS = ("/sys/class/tty/ttyACM*", "/sys/class/tty/ttyUSB*")


def _read_attr(directory: str, name: str) -> str | None:
    try:
        with open(os.path.join(directory, name), encoding="utf-8", errors="replace") as handle:
            return handle.read().strip()
    except OSError:
        return None


def _find_usb_device_dir(start: str) -> str | None:
    """Walk up from a tty's device link until the USB device node with idVendor."""
    current = os.path.realpath(start)
    for _ in range(8):
        if os.path.exists(os.path.join(current, "idVendor")):
            return current
        parent = os.path.dirname(current)
        if parent == current or not parent.startswith("/sys"):
            return None
        current = parent
    return None


def _by_id_paths() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for link in glob.glob("/dev/serial/by-id/*"):
        try:
            mapping[os.path.realpath(link)] = link
        except OSError:
            continue
    return mapping


def _holders(device_path: str) -> list[int]:
    """Best-effort list of PIDs holding the device open. Only sees our own processes."""
    pids: list[int] = []
    for fd_dir in glob.glob("/proc/[0-9]*/fd"):
        try:
            for fd in os.listdir(fd_dir):
                if os.readlink(os.path.join(fd_dir, fd)) == device_path:
                    pids.append(int(fd_dir.split("/")[2]))
                    break
        except (OSError, ValueError):
            continue
    return pids


def enumerate_boards() -> list[dict]:
    by_id = _by_id_paths()
    boards: list[dict] = []

    for pattern in TTY_GLOBS:
        for sys_path in sorted(glob.glob(pattern)):
            name = os.path.basename(sys_path)
            device_path = f"/dev/{name}"
            if not os.path.exists(device_path):
                continue

            usb_dir = _find_usb_device_dir(os.path.join(sys_path, "device"))
            if usb_dir is None:
                continue

            vid = (_read_attr(usb_dir, "idVendor") or "").lower()
            pid = (_read_attr(usb_dir, "idProduct") or "").lower()
            if not vid or not pid:
                continue

            info = identify(vid, pid)
            product = _read_attr(usb_dir, "product")
            holders = _holders(device_path)

            boards.append(
                {
                    "port": device_path,
                    "vid": vid,
                    "pid": pid,
                    "serial": _read_attr(usb_dir, "serial"),
                    "manufacturer": _read_attr(usb_dir, "manufacturer"),
                    "product": product,
                    "board_type": info.board_type,
                    "friendly_name": product or info.friendly_name,
                    "suggested_fqbn": info.fqbn,
                    "suggested_baud": info.baud,
                    "by_id_path": by_id.get(device_path),
                    "writable": os.access(device_path, os.R_OK | os.W_OK),
                    "busy": bool(holders),
                    "holder_pids": holders,
                }
            )

    return boards


def main() -> None:
    payload = {"ok": True, "boards": enumerate_boards()}
    json.dump(payload, sys.stdout, separators=(",", ":"))
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
