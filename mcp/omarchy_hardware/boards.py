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

from .ids import (
    BOARDS,
    RP2_BOOT_FQBNS,
    RP2_BOOT_PIDS,
    RP_VID,
    STLINK_ONBOARD_PIDS,
    STM32_USB_ONLY,
    STM32_VID,
    identify,
    identify_nucleo,
    nucleo_boards,
)

TTY_GLOBS = ("/sys/class/tty/ttyACM*", "/sys/class/tty/ttyUSB*")
USB_DEVICES_GLOB = "/sys/bus/usb/devices/[0-9]*"
LABEL_GLOB = "/dev/disk/by-label/*"
SYS_BLOCK = "/sys/class/block"
PROC_MOUNTS = "/proc/mounts"
RP2_BOARD_IDS = ("RPI-RP2", "RPI-RP2350", "RP2350")
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


def _usb_dirs_with_tty() -> set[str]:
    found: set[str] = set()
    for pattern in TTY_GLOBS:
        for sys_path in glob.glob(pattern):
            usb_dir = _find_usb_device_dir(os.path.join(sys_path, "device"))
            if usb_dir:
                found.add(os.path.realpath(usb_dir))
    return found


def _parse_info_uf2(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.startswith("Board-ID:"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        return None
    return None


def _has_hid_interface(usb_dir: str) -> bool:
    interfaces = os.path.join(usb_dir, os.path.basename(usb_dir) + ":*")
    return any((_read_attr(interface, "bInterfaceClass") or "").lower() == "03"
               for interface in glob.glob(interfaces))


# /proc/mounts octal-escapes space, tab, newline and backslash. Decode those
# tokens once; unicode_escape would corrupt UTF-8, and a second pass would turn
# "\134040" (a literal \040) into a space.
_MOUNT_OCTAL = {"040": " ", "011": "\t", "012": "\n", "134": "\\"}


def _decode_mounts_path(escaped: str) -> str:
    pieces: list[str] = []
    index = 0
    length = len(escaped)
    while index < length:
        if escaped[index] == "\\" and index + 3 < length:
            replacement = _MOUNT_OCTAL.get(escaped[index + 1 : index + 4])
            if replacement is not None:
                pieces.append(replacement)
                index += 4
                continue
        pieces.append(escaped[index])
        index += 1
    return "".join(pieces)


def _mountpoint_for_usb(usb_dir: str) -> str | None:
    usb_real = os.path.realpath(usb_dir) + os.sep
    try:
        with open(PROC_MOUNTS, encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()
    except OSError:
        return None
    for line in lines:
        parts = line.split()
        if len(parts) < 2 or not parts[0].startswith("/dev/"):
            continue
        source = parts[0]
        dest = _decode_mounts_path(parts[1])
        name = os.path.basename(os.path.realpath(source))
        sys_block = os.path.join(SYS_BLOCK, name)
        try:
            block_real = os.path.realpath(sys_block)
        except OSError:
            continue
        if block_real.startswith(usb_real) and os.path.isfile(os.path.join(dest, "INFO_UF2.TXT")):
            return dest
    return None


def enumerate_rp2_devices() -> list[dict]:
    """List RP2040/RP2350 boards that have no tty: BOOTSEL UF2 volumes and HID-only."""
    tty_usbs = _usb_dirs_with_tty()
    devices: list[dict] = []
    for usb_dir in sorted(glob.glob(USB_DEVICES_GLOB)):
        if ":" in os.path.basename(usb_dir):
            continue
        vid = (_read_attr(usb_dir, "idVendor") or "").lower()
        pid = (_read_attr(usb_dir, "idProduct") or "").lower()
        if vid != RP_VID:
            continue
        usb_real = os.path.realpath(usb_dir)
        if usb_real in tty_usbs:
            continue

        info = identify(vid, pid)
        product = _read_attr(usb_dir, "product")
        serial = _read_attr(usb_dir, "serial")
        if pid in RP2_BOOT_PIDS:
            mount = _mountpoint_for_usb(usb_dir)
            board_id = _parse_info_uf2(os.path.join(mount, "INFO_UF2.TXT")) if mount else None
            port = mount or f"usb:{os.path.basename(usb_dir)}"
            devices.append(
                {
                    "port": port,
                    "vid": vid,
                    "pid": pid,
                    "serial": serial,
                    "manufacturer": _read_attr(usb_dir, "manufacturer"),
                    "product": product,
                    "board_type": info.board_type,
                    "friendly_name": info.friendly_name,
                    "suggested_fqbn": info.fqbn,
                    "suggested_baud": 115200,
                    "by_id_path": None,
                    "writable": bool(mount) and os.access(mount, os.R_OK | os.W_OK),
                    "busy": False,
                    "holder_pids": [],
                    "kind": "uf2_bootloader",
                    "volume": mount,
                    "board_id": board_id,
                    "compatible_fqbns": list(RP2_BOOT_FQBNS[pid]),
                    "identity_note": "BOOTSEL identifies the chip family only; select your physical board's FQBN.",
                }
            )
            continue

        # Raspberry Pi's VID also covers hubs, keyboards and debug probes.
        # Neither the vendor nor the absence of a tty establishes a Pico HID.
        if info.board_type == "unknown" or not _has_hid_interface(usb_dir):
            continue
        devices.append(
            {
                "port": f"usb:{os.path.basename(usb_dir)}",
                "vid": vid,
                "pid": pid,
                "serial": serial,
                "manufacturer": _read_attr(usb_dir, "manufacturer"),
                "product": product,
                "board_type": info.board_type,
                "friendly_name": product or info.friendly_name,
                "suggested_fqbn": info.fqbn,
                "suggested_baud": info.baud,
                "by_id_path": None,
                "writable": False,
                "busy": False,
                "holder_pids": [],
                "kind": "hid",
            }
        )
    return devices


def all_boards() -> list[dict]:
    """Serial boards plus RP2 BOOTSEL/HID devices the tty scan cannot see."""
    return [*enumerate_boards(), *enumerate_rp2_devices()]


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
    return sorted(
        {
            info.friendly_name
            for info in known
            if info.board_type != "unknown" and info.fqbn and not info.board_type.endswith("_bootloader")
        }
    )


def _with_labels(boards: list[dict]) -> list[dict]:
    """Add the user's journal label to each board, for the panel. Best effort."""
    from . import journal  # Imported here: the journal is optional for enumeration.

    return [{**board, "label": journal.label(board)} for board in boards]


def main() -> None:
    payload = {"ok": True, "boards": _with_labels(all_boards()), "supported_boards": supported_board_names()}
    json.dump(payload, sys.stdout, separators=(",", ":"))
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
