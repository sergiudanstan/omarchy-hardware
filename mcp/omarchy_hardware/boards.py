"""Enumerate connected USB serial boards by walking sysfs.

Deliberately stdlib-only: the bar widget calls this before setup has run, when
pyserial may not be installed yet.
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys
import threading
import time

from .ids import BOARDS, STLINK_ONBOARD_PIDS, STM32_USB_ONLY, STM32_VID, identify, identify_nucleo, nucleo_boards

TTY_GLOBS = ("/sys/class/tty/ttyACM*", "/sys/class/tty/ttyUSB*")
USB_DEVICES_GLOB = "/sys/bus/usb/devices/[0-9]*"
LABEL_GLOB = "/dev/disk/by-label/*"
SYS_BLOCK = "/sys/class/block"
_HOLDERS_TTL = 3.0
_holders_cache: tuple[float, dict[str, list[int]]] | None = None
_holders_lock = threading.Lock()


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


def _volume_label(usb_dir: str) -> str | None:
    """Label of a disk exposed by this USB device, e.g. the Nucleo ST-LINK drive."""
    usb_real = os.path.realpath(usb_dir) + os.sep
    for link in glob.glob(LABEL_GLOB):
        try:
            block = os.path.basename(os.path.realpath(link))
            if os.path.realpath(os.path.join(SYS_BLOCK, block)).startswith(usb_real):
                # udev escapes unsafe characters in the link name as \xHH.
                return re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), os.path.basename(link))
        except OSError:
            continue
    return None


def _holders_by_device() -> dict[str, list[int]]:
    """Map device paths to PIDs of this user's processes that hold them open."""
    global _holders_cache
    now = time.monotonic()
    with _holders_lock:
        if _holders_cache is not None and now - _holders_cache[0] < _HOLDERS_TTL:
            return _holders_cache[1]

        mapping: dict[str, list[int]] = {}
        my_uid = os.getuid()
        for fd_dir in glob.glob("/proc/[0-9]*/fd"):
            try:
                proc_dir = os.path.dirname(fd_dir)
                if os.stat(proc_dir).st_uid != my_uid:
                    continue
                pid = int(os.path.basename(proc_dir))
                for fd in os.listdir(fd_dir):
                    target = os.readlink(os.path.join(fd_dir, fd))
                    mapping.setdefault(target, []).append(pid)
            except (OSError, ValueError):
                continue
        _holders_cache = (now, mapping)
        return mapping


def enumerate_boards() -> list[dict]:
    by_id = _by_id_paths()
    holders_by_device = _holders_by_device()
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
            friendly_name = product or info.friendly_name
            if vid == STM32_VID and pid in STLINK_ONBOARD_PIDS:
                nucleo = identify_nucleo(_volume_label(usb_dir))
                if nucleo is not None:
                    # The probe's product string ("STM32 STLink") would hide the board.
                    info, friendly_name = nucleo, nucleo.friendly_name
            holders = holders_by_device.get(device_path, [])

            boards.append(
                {
                    "port": device_path,
                    "vid": vid,
                    "pid": pid,
                    "serial": _read_attr(usb_dir, "serial"),
                    "manufacturer": _read_attr(usb_dir, "manufacturer"),
                    "product": product,
                    "board_type": info.board_type,
                    "friendly_name": friendly_name,
                    "suggested_fqbn": info.fqbn,
                    "suggested_baud": info.baud,
                    "by_id_path": by_id.get(device_path),
                    "writable": os.access(device_path, os.R_OK | os.W_OK),
                    "busy": bool(holders),
                    "holder_pids": holders,
                }
            )

    return boards


def enumerate_stm32_usb_devices() -> list[dict]:
    """List STM32 probes and DFU bootloaders, which have no tty for enumerate_boards."""
    devices: list[dict] = []
    for usb_dir in sorted(glob.glob(USB_DEVICES_GLOB)):
        # Interface nodes ("1-4:1.0") carry no idVendor; only device nodes count.
        if ":" in os.path.basename(usb_dir):
            continue
        vid = (_read_attr(usb_dir, "idVendor") or "").lower()
        pid = (_read_attr(usb_dir, "idProduct") or "").lower()
        if vid != STM32_VID or pid not in STM32_USB_ONLY:
            continue

        busnum = _read_attr(usb_dir, "busnum")
        devnum = _read_attr(usb_dir, "devnum")
        node = None
        if busnum and devnum and busnum.isdigit() and devnum.isdigit():
            node = f"/dev/bus/usb/{int(busnum):03d}/{int(devnum):03d}"

        devices.append(
            {
                "vid": vid,
                "pid": pid,
                "kind": "dfu_bootloader" if pid == "df11" else "debug_probe",
                "friendly_name": STM32_USB_ONLY[pid],
                "product": _read_attr(usb_dir, "product"),
                "serial": _read_attr(usb_dir, "serial"),
                "usb_node": node,
                # Probes and DFU need udev rules; without them the node is root-only.
                "writable": bool(node) and os.access(node, os.R_OK | os.W_OK),
            }
        )
    return devices


def supported_board_names() -> list[str]:
    """Share identifiable board targets with the widget, without duplicate USB IDs."""
    known = [*BOARDS.values(), *nucleo_boards()]
    return sorted({info.friendly_name for info in known if info.board_type != "unknown" and info.fqbn})


def _with_labels(boards: list[dict]) -> list[dict]:
    """Add the user's journal label to each board, for the panel. Best effort."""
    from . import journal  # Imported here: the journal is optional for enumeration.

    return [{**board, "label": journal.label(board)} for board in boards]


def main() -> None:
    payload = {"ok": True, "boards": _with_labels(enumerate_boards()), "supported_boards": supported_board_names()}
    json.dump(payload, sys.stdout, separators=(",", ":"))
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
