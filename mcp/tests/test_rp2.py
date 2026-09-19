import pytest

from omarchy_hardware import boards, policy
from omarchy_hardware.errors import ToolError
from omarchy_hardware.flash import _uf2_from_snapshot


def _usb_device(root, name, vid, pid, busnum="1", devnum="7", serial=None, product=None):
    device = root / name
    device.mkdir(parents=True)
    for attr, value in (("idVendor", vid), ("idProduct", pid), ("busnum", busnum), ("devnum", devnum)):
        (device / attr).write_text(value + "\n", encoding="utf-8")
    if serial:
        (device / "serial").write_text(serial + "\n", encoding="utf-8")
    if product:
        (device / "product").write_text(product + "\n", encoding="utf-8")
    return device


def test_bootsel_pico_is_listed_without_a_tty(tmp_path, monkeypatch):
    _usb_device(tmp_path / "sys", "1-2", "2e8a", "0003", serial="E0C9125B0D9B", product="RP2 Boot")
    monkeypatch.setattr(boards, "USB_DEVICES_GLOB", str(tmp_path / "sys" / "[0-9]*"))
    monkeypatch.setattr(boards, "TTY_GLOBS", (str(tmp_path / "missing" / "ttyACM*"),))
    monkeypatch.setattr(boards, "SYS_BLOCK", str(tmp_path / "block"))
    monkeypatch.setattr(boards, "PROC_MOUNTS", str(tmp_path / "mounts"))
    (tmp_path / "block").mkdir()
    (tmp_path / "mounts").write_text("", encoding="utf-8")

    [device] = boards.enumerate_rp2_devices()

    assert device["kind"] == "uf2_bootloader"
    assert device["board_type"] == "rp2040_bootloader"
    assert device["suggested_fqbn"] == "rp2040:rp2040:rpipico"
    assert device["port"] == "usb:1-2"
    assert device["serial"] == "E0C9125B0D9B"
    assert device["writable"] is False


def test_rp2_board_id_selects_pico2_fqbn():
    assert boards._fqbn_for_rp2_board_id("RPI-RP2") == "rp2040:rp2040:rpipico"
    assert boards._fqbn_for_rp2_board_id("RPI-RP2350") == "rp2040:rp2040:rpipico2"


def test_hid_only_pico_is_listed_when_there_is_no_tty(tmp_path, monkeypatch):
    _usb_device(tmp_path / "sys", "1-3", "2e8a", "000b", serial="E46498769F483438", product="Pico")
    monkeypatch.setattr(boards, "USB_DEVICES_GLOB", str(tmp_path / "sys" / "[0-9]*"))
    monkeypatch.setattr(boards, "TTY_GLOBS", (str(tmp_path / "missing" / "ttyACM*"),))
    monkeypatch.setattr(boards, "SYS_BLOCK", str(tmp_path / "block"))
    monkeypatch.setattr(boards, "PROC_MOUNTS", str(tmp_path / "mounts"))
    (tmp_path / "block").mkdir()
    (tmp_path / "mounts").write_text("", encoding="utf-8")

    [device] = boards.enumerate_rp2_devices()

    assert device["kind"] == "hid"
    assert device["board_type"] == "rp2040"
    assert device["port"] == "usb:1-3"
    assert device["suggested_fqbn"] == "rp2040:rp2040:rpipico"


def test_pico_with_cdc_serial_is_not_duplicated_as_hid(tmp_path, monkeypatch):
    usb = _usb_device(tmp_path / "sys", "1-4", "2e8a", "000a", product="Pico")
    tty_dev = usb / "1-4:1.0" / "tty" / "ttyACM3"
    tty_dev.mkdir(parents=True)
    tty_class = tmp_path / "class" / "ttyACM3"
    tty_class.mkdir(parents=True)
    (tty_class / "device").symlink_to(tty_dev)
    monkeypatch.setattr(boards, "USB_DEVICES_GLOB", str(tmp_path / "sys" / "[0-9]*"))
    monkeypatch.setattr(boards, "TTY_GLOBS", (str(tmp_path / "class" / "ttyACM*"),))
    monkeypatch.setattr(boards, "_find_usb_device_dir", lambda start: str(usb))
    monkeypatch.setattr(boards, "SYS_BLOCK", str(tmp_path / "block"))
    monkeypatch.setattr(boards, "PROC_MOUNTS", str(tmp_path / "mounts"))
    (tmp_path / "block").mkdir()
    (tmp_path / "mounts").write_text("", encoding="utf-8")

    assert boards.enumerate_rp2_devices() == []


def test_resolve_uf2_volume_requires_rp2_info(tmp_path, monkeypatch):
    volume = tmp_path / "run" / "media" / "dan" / "RPI-RP2"
    volume.mkdir(parents=True)
    (volume / "INFO_UF2.TXT").write_text("UF2 Bootloader v3.0\nBoard-ID: RPI-RP2\n", encoding="utf-8")
    monkeypatch.setattr(policy, "UF2_MOUNT_PREFIXES", (str(tmp_path / "run" / "media") + "/",))

    assert policy.resolve_uf2_volume(str(volume)) == str(volume.resolve())

    other = tmp_path / "run" / "media" / "dan" / "USBSTICK"
    other.mkdir()
    (other / "INFO_UF2.TXT").write_text("not a pico\n", encoding="utf-8")
    with pytest.raises(ToolError) as excinfo:
        policy.resolve_uf2_volume(str(other))
    assert excinfo.value.code == "PORT_NOT_ALLOWED"


def test_uf2_from_snapshot_prefers_ino_uf2(tmp_path):
    (tmp_path / "sketch.ino.uf2").write_bytes(b"uf2")
    (tmp_path / "other.uf2").write_bytes(b"no")
    assert _uf2_from_snapshot(str(tmp_path)).endswith("sketch.ino.uf2")
