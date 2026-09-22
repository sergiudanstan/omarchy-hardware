import json
import os
import subprocess
from pathlib import Path

import pytest

from omarchy_hardware import audit, boards, flash, policy, server
from omarchy_hardware.config import Config
from omarchy_hardware.errors import ToolError
from omarchy_hardware.flash import _uf2_from_snapshot

FQBN = "rp2040:rp2040:rpipico"


@pytest.fixture(autouse=True)
def _private_audit(tmp_path_factory, monkeypatch):
    monkeypatch.setattr(audit, "STATE_DIR", tmp_path_factory.mktemp("audit"))


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


@pytest.mark.parametrize("pid,board_type,fqbn", [
    ("0003", "rp2040_bootloader", FQBN),
    ("000f", "rp2350_bootloader", "rp2040:rp2040:rpipico2"),
])
def test_bootsel_pico_is_listed_without_a_mount(tmp_path, monkeypatch, pid, board_type, fqbn):
    _usb_device(tmp_path / "sys", "1-2", "2e8a", pid, serial="E0C9125B0D9B", product="RP Boot")
    monkeypatch.setattr(boards, "USB_DEVICES_GLOB", str(tmp_path / "sys" / "[0-9]*"))
    monkeypatch.setattr(boards, "TTY_GLOBS", (str(tmp_path / "missing" / "ttyACM*"),))
    monkeypatch.setattr(boards, "SYS_BLOCK", str(tmp_path / "block"))
    monkeypatch.setattr(boards, "PROC_MOUNTS", str(tmp_path / "mounts"))
    (tmp_path / "block").mkdir()
    (tmp_path / "mounts").write_text("", encoding="utf-8")

    [device] = boards.enumerate_rp2_devices()

    assert device["kind"] == "uf2_bootloader"
    assert device["board_type"] == board_type
    assert device["suggested_fqbn"] == fqbn
    assert device["port"] == "usb:1-2"
    assert device["serial"] == "E0C9125B0D9B"
    assert device["writable"] is False


@pytest.mark.parametrize("pid,board_id,fqbn,accepted", [
    ("0003", "RPI-RP2", FQBN, True),
    ("000f", "RP2350", "rp2040:rp2040:rpipico2", True),
    ("0003", "RPI-RP2", "rp2040:rp2040:rpipicow", True),
    ("000f", "RP2350", "rp2040:rp2040:rpipico2w", True),
    ("0003", "RPI-RP2", "rp2040:rp2040:rpipicow:usbstack=tinyusb", True),
    ("0003", "RPI-RP2", "rp2040:rp2040:rpipico2w", False),
    ("000f", "RP2350", "rp2040:rp2040:rpipicow", False),
    ("0003", "RPI-RP2", "arduino:avr:uno", False),
    ("0003", "RPI-RP2", "rp2040:rp2040:unknown", False),
])
def test_bootsel_discovery_and_server_upload(tmp_path, monkeypatch, pid, board_id, fqbn, accepted):
    usb = _usb_device(tmp_path / "sys", "1-2", "2e8a", pid, serial="E0C9")
    (usb / "disk" / "sda1").mkdir(parents=True)
    block = tmp_path / "block"
    block.mkdir()
    (block / "sda1").symlink_to(usb / "disk" / "sda1")
    volume = tmp_path / "media" / board_id
    volume.mkdir(parents=True)
    (volume / "INFO_UF2.TXT").write_text(f"Board-ID: {board_id}\n", encoding="utf-8")
    mounts = tmp_path / "mounts"
    mounts.write_text(f"/dev/sda1 {volume} vfat rw 0 0\n", encoding="utf-8")
    monkeypatch.setattr(boards, "USB_DEVICES_GLOB", str(tmp_path / "sys" / "[0-9]*"))
    monkeypatch.setattr(boards, "TTY_GLOBS", ())
    monkeypatch.setattr(boards, "SYS_BLOCK", str(block))
    monkeypatch.setattr(boards, "PROC_MOUNTS", str(mounts))
    monkeypatch.setattr(policy, "UF2_MOUNT_PREFIXES", (str(tmp_path / "media") + "/",))
    monkeypatch.setattr(server, "enumerate_boards", lambda: [])
    monkeypatch.setattr(server, "_config", lambda: Config(allow_flash=True, sketch_roots=(str(tmp_path),)))
    monkeypatch.setattr(server, "_flash_budget", None)

    listed = server.list_boards()
    assert listed["ok"] is True
    [device] = listed["boards"]
    assert device["kind"] == "uf2_bootloader"
    assert device["port"] == str(volume)
    assert device["volume"] == str(volume)
    assert device["writable"] is True
    assert "chip family only" in device["identity_note"]
    assert (":".join(fqbn.split(":")[:3]) in device["compatible_fqbns"]) == accepted
    sketch, artifact, digest, token = _mint_artifact(tmp_path, {"sketch.ino.uf2": b"UF2"}, fqbn=fqbn)
    # Exercise serial binding through compile without a port, including wireless
    # variants. The resulting token must also pass the real upload verifier.
    if accepted:
        monkeypatch.setattr(flash, "_arduino_cli", lambda args: subprocess.CompletedProcess(
            args, 0, stdout=json.dumps({"builder_result": {"build_path": str(artifact)}}),
        ))
        compiled = server.compile_sketch(str(sketch), fqbn)
        assert compiled["usb_serial"] == "E0C9"
        token = compiled["upload_token"]
    result = server.upload_sketch(
        str(sketch), device["port"], fqbn, token,
        artifact_path=str(artifact), artifact_digest=digest, confirm=True,
    )
    assert result["ok"] == accepted
    if accepted:
        assert (volume / "sketch.ino.uf2").read_bytes() == b"UF2"
    else:
        assert result["error"]["code"] == "BOARD_MISMATCH"
        assert not (volume / "sketch.ino.uf2").exists()


def test_hid_only_pico_is_listed_when_there_is_no_tty(tmp_path, monkeypatch):
    usb = _usb_device(tmp_path / "sys", "1-3", "2e8a", "000b", serial="E46498769F483438", product="Pico")
    interface = usb / "1-3:1.0"
    interface.mkdir()
    (interface / "bInterfaceClass").write_text("03\n", encoding="utf-8")
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


@pytest.mark.parametrize("pid,interface_class", [
    ("000c", "03"),  # Debug Probe, even if it exposes HID.
    ("000d", "09"),  # USB 2 hub.
    ("000e", "09"),  # USB 3 hub.
    ("0010", "03"),  # Pi 500 keyboard.
    ("ffff", "03"),  # An unknown HID is not enough to choose a Pico FQBN.
    ("000b", "08"),  # A known Pico PID without an HID interface.
    ("000b", None),  # Interfaces may not have appeared yet during enumeration.
])
def test_non_pico_or_non_hid_devices_are_not_listed(tmp_path, monkeypatch, pid, interface_class):
    usb = _usb_device(tmp_path / "sys", "1-3", "2e8a", pid)
    if interface_class is not None:
        interface = usb / "1-3:1.0"
        interface.mkdir()
        (interface / "bInterfaceClass").write_text(interface_class + "\n", encoding="utf-8")
    monkeypatch.setattr(boards, "USB_DEVICES_GLOB", str(tmp_path / "sys" / "[0-9]*"))
    monkeypatch.setattr(boards, "TTY_GLOBS", ())

    assert boards.enumerate_rp2_devices() == []


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


def test_uf2_from_snapshot_rejects_zero_and_multiple(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ToolError) as zero:
        _uf2_from_snapshot(str(empty))
    assert zero.value.code == "ARTIFACT_INVALID"

    two = tmp_path / "two"
    two.mkdir()
    (two / "a.uf2").write_bytes(b"one")
    (two / "b.uf2").write_bytes(b"two")
    with pytest.raises(ToolError) as multi:
        _uf2_from_snapshot(str(two))
    assert multi.value.code == "ARTIFACT_INVALID"


def test_decode_mounts_path_keeps_unicode_and_does_not_rescan_backslash():
    assert boards._decode_mounts_path("/run/media/dan/Pico") == "/run/media/dan/Pico"
    assert boards._decode_mounts_path("/run/media/dan-ă/RPI-RP2") == "/run/media/dan-ă/RPI-RP2"
    assert boards._decode_mounts_path("/run/media/dan/RP2\\040vol") == "/run/media/dan/RP2 vol"
    assert boards._decode_mounts_path("/media/foo\\134040bar") == "/media/foo\\040bar"
    assert " " not in boards._decode_mounts_path("/media/foo\\134040bar")


def _usb_block(root: Path):
    usb = root / "usb" / "1-2"
    (usb / "disk").mkdir(parents=True)
    block = root / "block"
    block.mkdir()
    (block / "sda1").symlink_to(usb / "disk")
    return usb, block


def test_mountpoint_finds_ascii_unicode_and_escaped_space(tmp_path, monkeypatch):
    usb, block = _usb_block(tmp_path)
    monkeypatch.setattr(boards, "SYS_BLOCK", str(block))

    def mount_named(name: str, mounts_text: str) -> str | None:
        volume = tmp_path / name
        volume.mkdir()
        (volume / "INFO_UF2.TXT").write_text("Board-ID: RPI-RP2\n", encoding="utf-8")
        mounts = tmp_path / f"mounts-{name}"
        mounts.write_text(mounts_text, encoding="utf-8")
        monkeypatch.setattr(boards, "PROC_MOUNTS", str(mounts))
        return boards._mountpoint_for_usb(str(usb))

    ascii_vol = tmp_path / "Pico"
    assert mount_named("Pico", f"/dev/sda1 {ascii_vol} vfat rw 0 0\n") == str(ascii_vol)

    unicode_vol = tmp_path / "Placă"
    assert mount_named("Placă", f"/dev/sda1 {unicode_vol} vfat rw 0 0\n") == str(unicode_vol)

    spaced = tmp_path / "RP2 vol"
    escaped = str(spaced).replace(" ", "\\040")
    assert mount_named("RP2 vol", f"/dev/sda1 {escaped} vfat rw 0 0\n") == str(spaced)


def test_resolve_uf2_volume_reads_board_id_field(tmp_path, monkeypatch):
    prefix = tmp_path / "run" / "media"
    monkeypatch.setattr(policy, "UF2_MOUNT_PREFIXES", (str(prefix) + "/",))

    def volume(name: str, text: str) -> Path:
        path = prefix / "dan" / name
        path.mkdir(parents=True)
        (path / "INFO_UF2.TXT").write_text(text, encoding="utf-8")
        return path

    rp2 = volume("RPI-RP2", "UF2 Bootloader v3.0\nBoard-ID: RPI-RP2\n")
    assert policy.resolve_uf2_volume(str(rp2)) == str(rp2.resolve())
    assert policy.resolve_uf2_volume(str(volume("RP2350", "Board-ID: RP2350\n")))
    assert policy.resolve_uf2_volume(str(volume("Pico2", "Board-ID: RPI-RP2350\n")))

    spoofed = volume("STICK", "Description: RPI-RP2\nBoard-ID: UNRELATED\n")
    with pytest.raises(ToolError) as misplaced:
        policy.resolve_uf2_volume(str(spoofed))
    assert misplaced.value.code == "PORT_NOT_ALLOWED"

    body_only = volume("NOTES", "this mentions RPI-RP2 and RP2350\n")
    with pytest.raises(ToolError) as no_field:
        policy.resolve_uf2_volume(str(body_only))
    assert no_field.value.code == "PORT_NOT_ALLOWED"


def _mint_artifact(tmp_path: Path, files: dict[str, bytes], *, fqbn: str = FQBN):
    sketch = tmp_path / "sketch"
    artifact = tmp_path / "artifact"
    sketch.mkdir()
    artifact.mkdir()
    for name, payload in files.items():
        path = artifact / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    digest = flash._artifact_digest(str(artifact))
    token = flash.mint_token(str(sketch), fqbn, "E0C9", str(artifact), digest)
    return sketch, artifact, digest, token


def test_missing_uf2_does_not_call_before_write(tmp_path):
    sketch, artifact, digest, token = _mint_artifact(tmp_path, {"sketch.hex": b"not a uf2"})
    volume = tmp_path / "volume"
    volume.mkdir()
    events: list[str] = []

    with pytest.raises(ToolError) as excinfo:
        flash.upload_sketch(
            str(sketch),
            str(volume),
            FQBN,
            token,
            serial="E0C9",
            artifact_path=str(artifact),
            artifact_digest=digest,
            roots=(str(tmp_path),),
            before_write=lambda: events.append("before_write"),
        )
    assert excinfo.value.code == "ARTIFACT_INVALID"
    assert events == []
    assert list(volume.iterdir()) == []


def test_server_missing_uf2_does_not_charge_budget_or_close_session(tmp_path, monkeypatch):
    sketch, artifact, digest, token = _mint_artifact(tmp_path, {"sketch.hex": b"not a uf2"})
    volume = tmp_path / "volume"
    volume.mkdir()
    (volume / "INFO_UF2.TXT").write_text("Board-ID: RPI-RP2\n", encoding="utf-8")
    board = {
        "port": str(volume.resolve()),
        "vid": "2e8a",
        "pid": "0003",
        "serial": "E0C9",
        "board_type": "rp2040_bootloader",
        "suggested_fqbn": FQBN,
        "kind": "uf2_bootloader",
        "writable": True,
    }
    charges: list[str] = []
    closed: list[str] = []

    class Budget:
        limit = 30

        def charge(self, key: str) -> None:
            charges.append(key)

    class Session:
        baud = 115200
        session_id = "s1"

    monkeypatch.setattr(server, "_flash_budget_for", lambda config: Budget())
    monkeypatch.setattr(server, "_config", lambda: Config(allow_flash=True, sketch_roots=(str(tmp_path),)))
    monkeypatch.setattr(server.policy, "resolve_flash_target", lambda port: str(volume.resolve()))
    monkeypatch.setattr(server, "enumerate_boards", lambda: [])
    monkeypatch.setattr(server, "enumerate_rp2_devices", lambda: [board])
    monkeypatch.setattr(server.sessions, "by_port", lambda port: Session())
    monkeypatch.setattr(server.sessions, "close_port", lambda port: closed.append(port))

    result = server.upload_sketch(
        str(sketch),
        str(volume),
        FQBN,
        token,
        artifact_path=str(artifact),
        artifact_digest=digest,
        confirm=True,
    )
    assert result["ok"] is False
    assert result["error"]["code"] == "ARTIFACT_INVALID"
    assert charges == []
    assert closed == []


def test_uf2_upload_copies_onto_a_mock_volume(tmp_path):
    sketch, artifact, digest, token = _mint_artifact(tmp_path, {"sketch.ino.uf2": b"UF2"})
    volume = tmp_path / "volume"
    volume.mkdir()

    result = flash.upload_sketch(
        str(sketch),
        str(volume),
        FQBN,
        token,
        serial="E0C9",
        artifact_path=str(artifact),
        artifact_digest=digest,
        roots=(str(tmp_path),),
    )
    assert result["ok"] is True
    copied = volume / "sketch.ino.uf2"
    assert copied.is_file()
    assert copied.read_bytes() == b"UF2"


@pytest.mark.parametrize("sync_fails", [False, True])
def test_uf2_sync_precedes_success_and_elf_retention(tmp_path, monkeypatch, sync_fails):
    sketch, artifact, digest, token = _mint_artifact(
        tmp_path, {"sketch.ino.uf2": b"UF2", "sketch.ino.elf": b"ELF"},
    )
    volume = tmp_path / "volume"
    volume.mkdir()
    destination = volume / "sketch.ino.uf2"
    events = []
    real_fsync = os.fsync

    def sync(fd):
        if destination.exists() and os.path.samestat(os.fstat(fd), destination.stat()):
            # Reading through a separate descriptor proves flush preceded fsync.
            assert destination.read_bytes() == b"UF2"
            events.append("synced")
            if sync_fails:
                raise OSError("simulated USB writeback error")
        real_fsync(fd)

    def keep_elf(path):
        events.append("elf_kept")
        return True

    monkeypatch.setattr(os, "fsync", sync)
    monkeypatch.setattr(audit, "note", lambda event, **record: events.append(event))

    def upload():
        return flash.upload_sketch(
            str(sketch), str(volume), FQBN, token, serial="E0C9",
            artifact_path=str(artifact), artifact_digest=digest, roots=(str(tmp_path),), keep_elf=keep_elf,
        )

    if sync_fails:
        with pytest.raises(ToolError) as error:
            upload()
        assert error.value.code == "SERIAL_ERROR"
        # The started record is closed with the failure, not left open.
        assert events == ["synced", "upload_finished"]
    else:
        result = upload()
        assert result["ok"] is True
        assert events == ["synced", "elf_kept", "upload_finished"]


def test_uf2_writeback_error_after_the_board_rebooted_is_reported_as_flashed(tmp_path, monkeypatch):
    import errno
    import shutil

    sketch, artifact, digest, token = _mint_artifact(tmp_path, {"sketch.ino.uf2": b"UF2"})
    volume = tmp_path / "volume"
    volume.mkdir()
    (volume / "INFO_UF2.TXT").write_text("Board-ID: RPI-RP2\n", encoding="utf-8")
    notes = []

    real_fsync = os.fsync
    destination = volume / "sketch.ino.uf2"

    def reboot(fd):
        if not (destination.exists() and os.path.samestat(os.fstat(fd), destination.stat())):
            return real_fsync(fd)  # the audit log's own fsync
        # The ROM drops the disk once the last block lands.
        shutil.rmtree(volume)
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(os, "fsync", reboot)
    monkeypatch.setattr(audit, "note", lambda event, **record: notes.append((event, record.get("returncode"))))

    result = flash.upload_sketch(
        str(sketch), str(volume), FQBN, token, serial="E0C9",
        artifact_path=str(artifact), artifact_digest=digest, roots=(str(tmp_path),),
    )

    assert result["ok"] is True
    assert "rebooted" in result["note"]
    assert notes == [("upload_finished", 0)]


def test_uf2_copy_does_not_follow_a_planted_symlink(tmp_path, monkeypatch):
    sketch, artifact, digest, token = _mint_artifact(tmp_path, {"sketch.ino.uf2": b"UF2"})
    volume = tmp_path / "volume"
    volume.mkdir()
    victim = tmp_path / "victim"
    victim.write_text("keep", encoding="utf-8")
    (volume / "sketch.ino.uf2").symlink_to(victim)
    monkeypatch.setattr(audit, "note", lambda event, **record: None)

    with pytest.raises(ToolError) as error:
        flash.upload_sketch(
            str(sketch), str(volume), FQBN, token, serial="E0C9",
            artifact_path=str(artifact), artifact_digest=digest, roots=(str(tmp_path),),
        )

    assert error.value.code == "SERIAL_ERROR"
    assert victim.read_text(encoding="utf-8") == "keep"


def test_arduino_cli_timeout_closes_the_upload_record(tmp_path, monkeypatch):
    sketch, artifact, digest, token = _mint_artifact(tmp_path, {"sketch.ino.hex": b"HEX"})
    notes = []

    def hang(args, timeout=300):
        raise ToolError("SERIAL_ERROR", "arduino-cli timed out.")

    monkeypatch.setattr(flash, "_arduino_cli", hang)
    monkeypatch.setattr(audit, "note", lambda event, **record: notes.append((event, record.get("error"))))

    with pytest.raises(ToolError):
        flash.upload_sketch(
            str(sketch), "/dev/ttyACM0", FQBN, token, serial="E0C9",
            artifact_path=str(artifact), artifact_digest=digest, roots=(str(tmp_path),),
        )

    assert notes == [("upload_finished", "SERIAL_ERROR")]


def test_run_group_kills_children_on_timeout(tmp_path):
    marker = tmp_path / "child.pid"
    script = f"sleep 30 & echo $! > {marker}; wait"
    with pytest.raises(subprocess.TimeoutExpired):
        flash.run_group(["/bin/sh", "-c", script], timeout=0.5)
    child = int(marker.read_text(encoding="utf-8"))
    import time

    for _ in range(50):
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("the child uploader outlived the timeout")
