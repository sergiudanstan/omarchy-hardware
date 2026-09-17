from omarchy_hardware import boards, server
from omarchy_hardware.config import Config


def _usb_device(root, name, vid, pid, busnum="1", devnum="7", serial=None):
    device = root / name
    device.mkdir(parents=True)
    for attr, value in (("idVendor", vid), ("idProduct", pid), ("busnum", busnum), ("devnum", devnum)):
        (device / attr).write_text(value + "\n", encoding="utf-8")
    if serial:
        (device / "serial").write_text(serial + "\n", encoding="utf-8")
    return device


def test_finds_stlink_v2_and_dfu_bootloader_but_not_other_devices(tmp_path, monkeypatch):
    _usb_device(tmp_path, "1-4", "0483", "3748", devnum="7")
    _usb_device(tmp_path, "1-5", "0483", "df11", devnum="12")
    _usb_device(tmp_path, "1-6", "2341", "0043")
    _usb_device(tmp_path, "1-7", "0483", "374b")  # V2-1 has a tty; enumerate_boards covers it
    (tmp_path / "1-4:1.0").mkdir()
    monkeypatch.setattr(boards, "USB_DEVICES_GLOB", str(tmp_path / "[0-9]*"))

    found = boards.enumerate_stm32_usb_devices()

    assert [(d["pid"], d["kind"]) for d in found] == [("3748", "debug_probe"), ("df11", "dfu_bootloader")]
    assert found[0]["usb_node"] == "/dev/bus/usb/001/007"
    assert found[1]["friendly_name"] == "STM32 DFU bootloader"


def test_hardware_report_includes_redacted_stm32_usb_devices(monkeypatch):
    monkeypatch.setattr(server, "_config", Config)
    monkeypatch.setattr(server, "enumerate_boards", lambda: [])
    monkeypatch.setattr(
        server,
        "enumerate_stm32_usb_devices",
        lambda: [{"vid": "0483", "pid": "3748", "serial": "STLINKSECRET", "kind": "debug_probe"}],
    )
    monkeypatch.setattr(server.sessions, "all", lambda: [])

    result = server.hardware_report()

    assert result["stm32_usb_devices"][0]["pid"] == "3748"
    assert "STLINKSECRET" not in str(result)


def _fake_stlink_tty(tmp_path, monkeypatch, label):
    """sysfs + /dev layout of a Nucleo: one ST-LINK exposing a tty and a labelled disk."""
    usb = _usb_device(tmp_path / "sys", "1-2", "0483", "374b")
    (usb / "product").write_text("STM32 STLink\n", encoding="utf-8")
    tty_dev = usb / "1-2:1.2" / "tty" / "ttyACM9"
    tty_dev.mkdir(parents=True)
    tty_class = tmp_path / "class" / "ttyACM9"
    tty_class.mkdir(parents=True)
    (tty_class / "device").symlink_to(tty_dev)
    disk = usb / "1-2:1.1" / "host3" / "block" / "sdz"
    disk.mkdir(parents=True)
    block = tmp_path / "block"
    block.mkdir()
    (block / "sdz").symlink_to(disk)
    by_label = tmp_path / "by-label"
    by_label.mkdir()
    (by_label / label).symlink_to(tmp_path / "dev" / "sdz")

    monkeypatch.setattr(boards, "TTY_GLOBS", (str(tmp_path / "class" / "ttyACM*"),))
    monkeypatch.setattr(boards, "LABEL_GLOB", str(by_label / "*"))
    monkeypatch.setattr(boards, "SYS_BLOCK", str(block))
    monkeypatch.setattr(boards, "_by_id_paths", dict)
    monkeypatch.setattr(boards, "_holders_by_device", dict)
    monkeypatch.setattr(boards.os.path, "exists", lambda path: True)
    # The real walk refuses to leave /sys; point it straight at the fake device.
    monkeypatch.setattr(boards, "_find_usb_device_dir", lambda start: str(usb))


def test_nucleo_is_identified_from_stlink_drive_label(tmp_path, monkeypatch):
    _fake_stlink_tty(tmp_path, monkeypatch, "NOD_F411RE")

    [board] = boards.enumerate_boards()

    assert board["port"] == "/dev/ttyACM9"
    assert board["board_type"] == "stm32_nucleo"
    assert board["friendly_name"] == "STM32 Nucleo-F411RE"
    assert board["suggested_fqbn"] == "STMicroelectronics:stm32:Nucleo_64:pnum=NUCLEO_F411RE"
    assert board["product"] == "STM32 STLink"


def test_stlink_without_nucleo_label_stays_unknown(tmp_path, monkeypatch):
    _fake_stlink_tty(tmp_path, monkeypatch, "SOME_DISK")

    [board] = boards.enumerate_boards()

    assert board["board_type"] == "unknown"
    assert board["friendly_name"] == "STM32 STLink"
    assert board["suggested_fqbn"] is None
