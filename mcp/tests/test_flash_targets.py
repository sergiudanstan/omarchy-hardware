"""Regression tests for the 2026-09-22 review of flash targets, budgets and board identity."""

import pytest

from omarchy_hardware import backup, boards, errors, journal, policy, server
from omarchy_hardware.config import Config

UNO = {"port": "/dev/ttyACM0", "vid": "2341", "pid": "0043", "serial": "A1", "board_type": "arduino_uno",
       "suggested_fqbn": "arduino:avr:uno", "busy": False, "holder_pids": []}


def _usb_tty(root, tty, vid, pid, product=None, serial="S1"):
    usb = root / "usb" / "1-4"
    usb.mkdir(parents=True)
    for name, value in (("idVendor", vid), ("idProduct", pid), ("serial", serial)):
        (usb / name).write_text(value + "\n", encoding="utf-8")
    if product:
        (usb / "product").write_text(product + "\n", encoding="utf-8")
    interface = usb / "1-4:1.0"
    interface.mkdir()
    node = root / "class" / tty
    node.mkdir(parents=True)
    (node / "device").symlink_to(interface)
    return usb


def test_a_changed_limit_keeps_what_was_already_spent(monkeypatch):
    monkeypatch.setattr(server, "_flash_budget", None)
    budget = server._flash_budget_for(Config(max_uploads_per_hour=30))
    for _ in range(25):
        budget.charge("board")
    lowered = server._flash_budget_for(Config(max_uploads_per_hour=10))
    assert lowered is budget
    with pytest.raises(errors.ToolError) as caught:
        lowered.charge("board")
    assert caught.value.code == errors.RATE_LIMITED


def test_a_serial_path_keeps_its_serial_error():
    with pytest.raises(errors.ToolError) as caught:
        policy.resolve_flash_target("/dev/ttyACM97")
    assert caught.value.code == errors.PORT_NOT_FOUND
    assert "UF2" not in caught.value.message
    with pytest.raises(errors.ToolError) as caught:
        policy.resolve_flash_target("/home/user/somewhere")
    assert caught.value.code == errors.PORT_NOT_ALLOWED
    assert "/dev/ttyACM*" in caught.value.hint


def test_every_rp2040_in_bootsel_shares_one_serial_so_none_is_tracked():
    bootsel = {"vid": "2e8a", "pid": "0003", "serial": "E0C9125B0D9B"}
    assert journal.board_key(bootsel) is None
    assert journal.board_key({**bootsel, "pid": "000a", "serial": "E6614C311B7A2A2F"}) is not None


def test_a_running_pico_2_is_not_listed_as_bootsel(tmp_path, monkeypatch):
    usb = _usb_tty(tmp_path, "ttyACM0", "2e8a", "000f", product="Pico 2")
    monkeypatch.setattr(boards, "TTY_GLOBS", (str(tmp_path / "class" / "ttyACM*"),))
    monkeypatch.setattr(boards, "_find_usb_device_dir", lambda start: str(usb))
    monkeypatch.setattr(boards.os.path, "exists", lambda path: True)
    monkeypatch.setattr(boards, "_by_id_paths", dict)
    monkeypatch.setattr(boards, "_holders_by_device", dict)

    [board] = boards.enumerate_boards()

    assert board["board_type"] == "rp2350"
    assert board["friendly_name"] == "Pico 2"
    assert board["suggested_fqbn"] == "rp2040:rp2040:rpipico2"
    assert board["compatible_fqbns"] == ["rp2040:rp2040:rpipico2", "rp2040:rp2040:rpipico2w"]


def test_only_a_fat_volume_counts_as_a_bootsel_drive(tmp_path, monkeypatch):
    usb = tmp_path / "usb" / "1-2"
    (usb / "disk" / "sda1").mkdir(parents=True)
    block = tmp_path / "block"
    block.mkdir()
    (block / "sda1").symlink_to(usb / "disk" / "sda1")
    volume = tmp_path / "media" / "RPI-RP2"
    volume.mkdir(parents=True)
    (volume / "INFO_UF2.TXT").write_text("Board-ID: RPI-RP2\n", encoding="utf-8")
    monkeypatch.setattr(boards, "SYS_BLOCK", str(block))
    mounts = tmp_path / "mounts"
    monkeypatch.setattr(boards, "PROC_MOUNTS", str(mounts))

    mounts.write_text(f"/dev/sda1 {volume} ext4 rw 0 0\n", encoding="utf-8")
    assert boards._mountpoint_for_usb(str(usb)) is None
    mounts.write_text(f"/dev/sda1 {volume} vfat rw 0 0\n", encoding="utf-8")
    assert boards._mountpoint_for_usb(str(usb)) == str(volume)


@pytest.mark.parametrize(
    ("board", "fqbn", "accepted"),
    [
        ({"suggested_fqbn": "esp32:esp32:esp32s3"}, "esp32:esp32:esp32s3:PSRAM=opi", True),
        ({"suggested_fqbn": "esp32:esp32:esp32s3"}, "esp32:esp32:esp32", False),
        ({"suggested_fqbn": "rp2040:rp2040:rpipico",
          "compatible_fqbns": ["rp2040:rp2040:rpipico", "rp2040:rp2040:rpipicow"]},
         "rp2040:rp2040:rpipicow:usbstack=tinyusb", True),
        ({"suggested_fqbn": "rp2040:rp2040:rpipico",
          "compatible_fqbns": ["rp2040:rp2040:rpipico", "rp2040:rp2040:rpipicow"]},
         "rp2040:rp2040:rpipico2", False),
        ({"suggested_fqbn": "STMicroelectronics:stm32:Nucleo_64:pnum=NUCLEO_F411RE"},
         "STMicroelectronics:stm32:Nucleo_64:pnum=NUCLEO_F030R8", False),
    ],
)
def test_menu_options_do_not_change_which_board_an_fqbn_names(board, fqbn, accepted):
    assert server._matches_flash_fqbn(board, fqbn) is accepted


def test_fingerprint_refuses_a_target_that_is_not_a_serial_port(monkeypatch):
    hid = {**UNO, "port": "usb:1-3", "vid": "2e8a", "pid": "000b", "kind": "hid"}
    monkeypatch.setattr(server, "enumerate_boards", lambda: [])
    monkeypatch.setattr(server, "enumerate_rp2_devices", lambda: [hid])
    assert server.fingerprint_board("usb:1-3")["error"]["code"] == errors.PORT_NOT_ALLOWED


def test_a_dead_session_does_not_block_and_our_own_pid_is_not_another_process(monkeypatch):
    closed = []

    class Dead:
        def status(self):
            return {"open": False}

    monkeypatch.setattr(server.sessions, "by_port", lambda port: Dead())
    monkeypatch.setattr(server.sessions, "close_port", closed.append)
    server._require_free_port({**UNO, "busy": True, "holder_pids": [server.os.getpid()]})
    assert closed == ["/dev/ttyACM0"]

    with pytest.raises(errors.ToolError) as caught:
        server._require_free_port({**UNO, "busy": True, "holder_pids": [1]})
    assert caught.value.code == errors.PORT_BUSY


def test_upload_refuses_a_bridged_port(monkeypatch):
    monkeypatch.setattr(server, "_config", lambda: Config(allow_flash=True, sketch_roots=("/s",)))
    monkeypatch.setattr(server.policy, "resolve_flash_target", lambda port: port)
    monkeypatch.setattr(server.policy, "check_readable", lambda port: None)
    monkeypatch.setattr(server.bridges, "by_port", lambda port: type("Running", (), {"bridge_id": "b1"})())
    result = server.upload_sketch("/s/x", "/dev/ttyACM0", "arduino:avr:uno", "t", confirm=True)
    assert result["error"]["code"] == errors.PORT_BUSY
    assert "bridged" in result["error"]["message"]


def test_native_usb_esp32_uploads_without_reading_its_mac(monkeypatch):
    native = {**UNO, "port": "/dev/ttyACM0", "vid": "303a", "pid": "1001", "board_type": "esp32",
              "suggested_fqbn": "esp32:esp32:esp32s3"}
    monkeypatch.setattr(server, "_config", lambda: Config(allow_flash=True, sketch_roots=("/s",)))
    monkeypatch.setattr(server, "_flash_budget", None)
    monkeypatch.setattr(server.policy, "resolve_flash_target", lambda port: port)
    monkeypatch.setattr(server.policy, "check_readable", lambda port: None)
    monkeypatch.setattr(server, "enumerate_boards", lambda: [native])
    monkeypatch.setattr(server, "enumerate_rp2_devices", lambda: [])
    monkeypatch.setattr(server.sessions, "by_port", lambda port: None)

    def no_mac(port):
        raise AssertionError("read-mac is only needed behind a USB-serial bridge")

    monkeypatch.setattr(backup, "identify", no_mac)

    def fake_upload(*args, before_write=None, **kwargs):
        before_write()
        return {"ok": True}

    monkeypatch.setattr(server.flash, "upload_sketch", fake_upload)
    assert server.upload_sketch("/s/x", "/dev/ttyACM0", "esp32:esp32:esp32s3", "t", confirm=True)["ok"] is True


def test_a_failed_flash_read_leaves_no_partial_image(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "identify", lambda port: {"mac": "24:0a:c4:aa:bb:cc", "chip": "ESP32"})
    monkeypatch.setattr(backup, "backups_root", lambda: tmp_path / "backups")

    def partial(port, *args, timeout=60):
        (tmp_path / "backups" / "240ac4aabbcc" / f".reading-{backup.os.getpid()}.bin").write_bytes(b"F" * 10)
        raise errors.ToolError(errors.SERIAL_ERROR, "esptool timed out.")

    monkeypatch.setattr(backup, "_esptool", partial)
    with pytest.raises(errors.ToolError):
        backup.create("/dev/ttyUSB0")
    assert list((tmp_path / "backups" / "240ac4aabbcc").iterdir()) == []
