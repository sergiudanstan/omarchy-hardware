import json
import stat

import pytest

from omarchy_hardware import audit, backup, errors, fingerprint, server
from omarchy_hardware.config import Config

ESP = {"port": "/dev/ttyUSB0", "vid": "10c4", "pid": "ea60", "serial": "0001", "board_type": "unknown",
       "friendly_name": "Silicon Labs CP210x", "suggested_fqbn": None, "busy": False}
FAKE_ESPTOOL = """#!/bin/sh
echo "$@" >> "$FAKE_LOG"
while [ "$1" = "--port" ] || [ "$1" = "--baud" ]; do shift; shift; done
case "$1" in
  read-mac) echo "Chip type:          ESP32-D0WD-V3 (revision v3.1)"; echo "MAC:                ${FAKE_MAC}";;
  read-flash) head -c 4096 /dev/zero | tr '\\0' 'F' > "$5"; echo "Read 4096 bytes";;
  write-flash) cp "$3" "$FAKE_FLASHED"; echo "Hash of data verified.";;
  *) exit 2;;
esac
"""


@pytest.fixture
def esp(tmp_path, monkeypatch):
    tool = tmp_path / "esptool"
    tool.write_text(FAKE_ESPTOOL)
    tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("OMARCHY_HARDWARE_ESPTOOL", str(tool))
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "calls.log"))
    monkeypatch.setenv("FAKE_MAC", "24:0a:c4:aa:bb:cc")
    monkeypatch.setenv("FAKE_FLASHED", str(tmp_path / "flashed.bin"))
    monkeypatch.setattr(audit, "STATE_DIR", tmp_path / "state")
    audit._chain.reset()
    monkeypatch.setattr(server, "_flash_budget", None)
    monkeypatch.setattr(server.policy, "resolve_port", lambda port: ESP["port"])
    monkeypatch.setattr(server.policy, "check_readable", lambda port: None)
    monkeypatch.setattr(server, "enumerate_boards", lambda: [ESP])
    cache = fingerprint.Cache()
    cache.store(ESP, fingerprint.analyse(b"ets Jun  8 2016 00:22:57\r\n"))
    monkeypatch.setattr(server, "fingerprints", cache)
    monkeypatch.setattr(server, "_config", lambda: Config(allow_flash=True, sketch_roots=("/s",)))
    return tmp_path


def test_backup_reads_the_whole_flash_privately(esp):
    result = server.firmware_backup("/dev/ttyUSB0")
    assert result["ok"] is True
    assert result["mac"] == "24:0a:c4:aa:bb:cc" and result["chip"] == "ESP32-D0WD-V3" and result["bytes"] == 4096
    calls = (esp / "calls.log").read_text().splitlines()
    assert calls[0].startswith("--port /dev/ttyUSB0 --baud 460800 read-mac")
    assert " read-flash --no-progress 0 ALL " in calls[1]
    [image] = list(backup.backups_root().glob("*/*.bin"))
    assert stat.S_IMODE(image.stat().st_mode) == 0o600
    assert stat.S_IMODE(image.parent.stat().st_mode) == 0o700
    listed = server.firmware_backups("/dev/ttyUSB0")["backups"]
    assert [b["backup_id"] for b in listed] == [result["backup_id"]]


def test_only_the_last_three_are_kept(esp, monkeypatch):
    ids = []
    for n in range(5):
        def stamp(fmt, n=n):
            return f"2026091{n}-120000" if fmt == "%Y%m%d-%H%M%S" else "2026-09-19T12:00:00+0000"

        monkeypatch.setattr(backup.time, "strftime", stamp)
        ids.append(server.firmware_backup("/dev/ttyUSB0")["backup_id"])
    kept = [b["backup_id"] for b in server.firmware_backups("/dev/ttyUSB0")["backups"]]
    assert kept == ids[:1:-1] and len(kept) == 3


def test_restore_writes_only_to_the_same_chip(esp, monkeypatch):
    made = server.firmware_backup("/dev/ttyUSB0")
    assert server.firmware_restore("/dev/ttyUSB0", made["backup_id"])["error"]["code"] == errors.UNCONFIRMED

    monkeypatch.setenv("FAKE_MAC", "24:0a:c4:00:00:01")
    other = server.firmware_restore("/dev/ttyUSB0", made["backup_id"], confirm=True)
    assert other["error"]["code"] == errors.BOARD_MISMATCH
    assert not (esp / "flashed.bin").exists()

    monkeypatch.setenv("FAKE_MAC", "24:0a:c4:aa:bb:cc")
    restored = server.firmware_restore("/dev/ttyUSB0", made["backup_id"], confirm=True)
    assert restored["ok"] is True
    assert (esp / "flashed.bin").read_bytes() == b"F" * 4096
    events = [json.loads(line)["event"] for line in audit.log_path().read_text().splitlines()]
    assert events[-2:] == ["firmware_restore_started", "firmware_restore_done"]


def test_restore_refuses_tampered_or_unknown_backups(esp):
    made = server.firmware_backup("/dev/ttyUSB0")
    [image] = list(backup.backups_root().glob("*/*.bin"))
    image.write_bytes(b"tampered")
    assert server.firmware_restore("/dev/ttyUSB0", made["backup_id"], confirm=True)["error"]["code"] == \
        errors.ARTIFACT_INVALID
    for bad in ("../../etc/passwd", "20260919-120000-zzzzzzzzzzzz"):
        assert server.firmware_restore("/dev/ttyUSB0", bad, confirm=True)["error"]["code"] in (
            errors.INVALID_ARGUMENT, errors.ARTIFACT_INVALID)


def test_restore_respects_flash_settings(esp, monkeypatch):
    made = server.firmware_backup("/dev/ttyUSB0")
    monkeypatch.setattr(server, "_config", Config)
    assert server.firmware_restore("/dev/ttyUSB0", made["backup_id"], confirm=True)["error"]["code"] == \
        errors.FLASH_DISABLED
    monkeypatch.setattr(server, "_config",
                        lambda: Config(allow_flash=True, sketch_roots=("/s",), max_uploads_per_hour=1))
    server.firmware_restore("/dev/ttyUSB0", made["backup_id"], confirm=True)
    again = server.firmware_restore("/dev/ttyUSB0", made["backup_id"], confirm=True)
    assert again["error"]["code"] == errors.RATE_LIMITED


def test_non_esp_boards_and_busy_ports_are_refused(esp, monkeypatch):
    uno = {**ESP, "board_type": "arduino_uno", "suggested_fqbn": "arduino:avr:uno", "vid": "2341", "pid": "0043"}
    monkeypatch.setattr(server, "enumerate_boards", lambda: [uno])
    monkeypatch.setattr(server, "fingerprints", fingerprint.Cache())
    assert server.firmware_backup("/dev/ttyUSB0")["error"]["code"] == errors.UNSUPPORTED_OPERATION
    assert not (esp / "calls.log").exists(), "esptool never ran against a non-ESP board"

    native = {**ESP, "board_type": "esp32", "suggested_fqbn": "esp32:esp32:esp32s2"}
    monkeypatch.setattr(server, "enumerate_boards", lambda: [native])
    class Live:
        def status(self):
            return {"open": True}

    monkeypatch.setattr(server.sessions, "by_port", lambda port: Live())
    assert server.firmware_backup("/dev/ttyUSB0")["error"]["code"] == errors.PORT_BUSY


def test_esptool_lookup(tmp_path, monkeypatch):
    monkeypatch.setenv("OMARCHY_HARDWARE_ESPTOOL", "esptool")
    with pytest.raises(errors.ToolError, match="absolute"):
        backup.esptool_path()
    monkeypatch.delenv("OMARCHY_HARDWARE_ESPTOOL")
    monkeypatch.setenv("OMARCHY_HARDWARE_ARDUINO_DATA", str(tmp_path))
    with pytest.raises(errors.ToolError) as caught:
        backup.esptool_path()
    assert caught.value.code == errors.TOOL_MISSING
    tool = tmp_path / "packages" / "esp32" / "tools" / "esptool_py" / "5.3.1" / "esptool"
    tool.parent.mkdir(parents=True)
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o755)
    assert backup.esptool_path() == str(tool)


def test_mac_parsing_matches_esptool_5_output():
    classic = "Connected to ESP32 on /dev/ttyUSB0:\nChip type:          ESP32-D0WD-V3 (revision v3.1)\n" \
              "MAC:                24:0a:c4:aa:bb:cc\n"
    assert backup.MAC.search(classic).group(1) == "24:0a:c4:aa:bb:cc"
    assert backup.CHIP.search(classic).group(1) == "ESP32-D0WD-V3"
    eui64 = "MAC:                40:4c:ca:ff:fe:12:34:56\nBASE MAC:           40:4c:ca:12:34:56\n"
    assert backup.MAC.search(eui64).group(1) == "40:4c:ca:ff:fe:12:34:56"
    assert backup.MAC.search("BASE MAC:           40:4c:ca:12:34:56\n") is None


def test_two_chips_behind_identical_adapters_keep_separate_backups(esp, monkeypatch):
    # Both CP210x bridges report USB serial 0001: only the chip MAC tells them apart.
    ticks = iter(range(100))

    def stamp(fmt):
        return f"20260919-12{next(ticks):04d}" if fmt == "%Y%m%d-%H%M%S" else "2026-09-19T12:00:00+0000"

    monkeypatch.setattr(backup.time, "strftime", stamp)
    for _ in range(4):
        monkeypatch.setenv("FAKE_MAC", "24:0a:c4:aa:bb:cc")
        server.firmware_backup("/dev/ttyUSB0")
    monkeypatch.setenv("FAKE_MAC", "24:0a:c4:00:00:01")
    other = server.firmware_backup("/dev/ttyUSB0")
    macs = [b["mac"] for b in server.firmware_backups("/dev/ttyUSB0")["backups"]]
    assert macs.count("24:0a:c4:aa:bb:cc") == backup.KEEP
    assert macs.count("24:0a:c4:00:00:01") == 1
    for _ in range(4):
        monkeypatch.setenv("FAKE_MAC", "24:0a:c4:aa:bb:cc")
        server.firmware_backup("/dev/ttyUSB0")
    assert other["backup_id"] in [b["backup_id"] for b in server.firmware_backups("/dev/ttyUSB0")["backups"]], \
        "pruning one chip's backups never touches another chip's"


def test_restore_budget_is_only_charged_for_a_real_write(esp, monkeypatch):
    made = server.firmware_backup("/dev/ttyUSB0")
    monkeypatch.setattr(server, "_config",
                        lambda: Config(allow_flash=True, sketch_roots=("/s",), max_uploads_per_hour=1))
    monkeypatch.setenv("FAKE_MAC", "24:0a:c4:00:00:01")
    for _ in range(3):
        assert server.firmware_restore("/dev/ttyUSB0", made["backup_id"], confirm=True)["error"]["code"] == \
            errors.BOARD_MISMATCH
    monkeypatch.setenv("FAKE_MAC", "24:0a:c4:aa:bb:cc")
    assert server.firmware_restore("/dev/ttyUSB0", made["backup_id"], confirm=True)["ok"] is True
    events = [json.loads(line)["event"] for line in audit.log_path().read_text().splitlines()]
    assert events.count("firmware_restore_started") == 1, "refused restores leave no 'started' record"
