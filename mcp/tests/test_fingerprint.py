import os
import pty
import threading
import time

import pytest

from omarchy_hardware import audit, errors, fingerprint, server
from omarchy_hardware.config import Config

ESP32_BOOT = (
    b"ets Jun  8 2016 00:22:57\r\n\r\nrst:0x1 (POWERON_RESET),boot:0x13 (SPI_FAST_FLASH_BOOT)\r\n"
    b"configsip: 0, SPIWP:0xee\r\nmode:DIO, clock div:1\r\nload:0x3fff0030,len:1184\r\nentry 0x400805e4\r\n"
)
ESP32S3_BOOT = b"ESP-ROM:esp32s3-20210327\r\nBuild:Mar 27 2021\r\nrst:0x1 (POWERON),boot:0x8 (SPI_FAST_FLASH_BOOT)\r\n"
MICROPYTHON = b"MicroPython v1.22.2 on 2024-02-22; Raspberry Pi Pico with RP2040\r\nType \"help()\" for more.\r\n>>> "


def test_esp32_rom_banner_names_the_chip_and_profile():
    result = fingerprint.analyse(ESP32_BOOT)
    [match] = result["matches"]
    assert match["chip"] == "ESP32"
    assert match["suggested_fqbn"] == "esp32:esp32:esp32"
    assert match["profile_id"] == "esp32_devkitc"
    assert result["sample"][0].startswith("ets Jun")


def test_esp32_s3_banner():
    [match] = fingerprint.analyse(ESP32S3_BOOT)["matches"]
    assert (match["chip"], match["suggested_fqbn"], match["profile_id"]) == ("ESP32-S3", "esp32:esp32:esp32s3", None)


def test_runtime_banners():
    [micropython] = fingerprint.analyse(MICROPYTHON)["matches"]
    assert micropython == {"kind": "micropython", "version": "1.22.2", "board": "Raspberry Pi Pico", "mcu": "RP2040"}
    [circuitpython] = fingerprint.analyse(
        b"Adafruit CircuitPython 9.0.0 on 2024-03-19; Adafruit Feather RP2040 with rp2040\r\n"
    )["matches"]
    assert circuitpython["kind"] == "circuitpython" and circuitpython["board"] == "Adafruit Feather RP2040"


def test_own_firmware_line():
    [match] = fingerprint.analyse(b'garbage\n{"fw":"greenhouse","build":"Sep 19 2026"}\n{"selftest":true}\n')["matches"]
    assert match == {"kind": "omarchy_firmware", "fw": "greenhouse", "build": "Sep 19 2026"}


def test_noise_is_not_a_match_and_the_sample_is_bounded():
    noise = b"".join(b"\x00\x1b[31mline %d " % n + b"z" * 400 + b"\n" for n in range(50))
    result = fingerprint.analyse(noise)
    assert result["matches"] == []
    assert len(result["sample"]) == fingerprint.SAMPLE_LINES
    assert all(len(line) <= fingerprint.SAMPLE_CHARS and "\x1b" not in line for line in result["sample"])


BOARD = {"port": "/dev/ttyUSB0", "vid": "10c4", "pid": "ea60", "serial": "0001", "board_type": "unknown",
         "friendly_name": "Silicon Labs CP210x", "suggested_fqbn": None, "busy": False}


def test_cache_vouches_only_for_accepted_fqbns_on_the_same_device(monkeypatch):
    cache = fingerprint.Cache()
    cache.store(BOARD, fingerprint.analyse(ESP32_BOOT))
    assert cache.accepts(BOARD, "esp32:esp32:esp32")["chip"] == "ESP32"
    assert cache.accepts(BOARD, "esp32:esp32:esp32doit-devkit-v1") is not None
    assert cache.accepts(BOARD, "esp32:esp32:esp32s3") is None
    assert cache.accepts(BOARD, "arduino:avr:uno") is None
    assert cache.accepts({**BOARD, "serial": "0002"}, "esp32:esp32:esp32") is None
    assert cache.accepts({**BOARD, "port": "/dev/ttyUSB1"}, "esp32:esp32:esp32") is None

    later = time.monotonic() + fingerprint.CACHE_SECONDS + 1
    monkeypatch.setattr(fingerprint.time, "monotonic", lambda: later)
    assert cache.accepts(BOARD, "esp32:esp32:esp32") is None


def test_runtime_matches_do_not_vouch_for_flashing():
    cache = fingerprint.Cache()
    cache.store(BOARD, fingerprint.analyse(MICROPYTHON))
    assert cache.accepts(BOARD, "rp2040:rp2040:rpipico") is None


@pytest.fixture
def pty_board(monkeypatch):
    master, slave = pty.openpty()
    path = os.ttyname(slave)
    board = {**BOARD, "port": path}
    monkeypatch.setattr(server.policy, "resolve_port", lambda port: path)
    monkeypatch.setattr(server.policy, "check_readable", lambda port: None)
    monkeypatch.setattr(server, "enumerate_boards", lambda: [board])
    monkeypatch.setattr(fingerprint, "LISTEN_MS", 600)
    monkeypatch.setattr(server, "fingerprints", fingerprint.Cache())
    yield master, path, board
    os.close(master)
    os.close(slave)


def test_fingerprint_tool_listens_then_closes(pty_board):
    master, path, board = pty_board
    threading.Timer(0.15, lambda: os.write(master, ESP32_BOOT)).start()
    result = server.fingerprint_board(path)
    assert result["ok"] is True and result["untrusted"] is True
    assert result["identified"] is True
    assert result["suggested_fqbn"] == "esp32:esp32:esp32"
    assert server.sessions.by_port(path) is None, "the port is closed again"
    assert server.fingerprints.accepts(board, "esp32:esp32:esp32") is not None


def test_fingerprint_tool_refuses_an_open_session(pty_board):
    _master, path, _board = pty_board
    session = server.sessions.open(path, 115200)
    try:
        assert server.fingerprint_board(path)["error"]["code"] == errors.PORT_BUSY
    finally:
        server.sessions.close(session.session_id)


def _upload(monkeypatch, tmp_path, board, config, fqbn="esp32:esp32:esp32"):
    monkeypatch.setattr(audit, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(server, "_config", lambda: config)
    monkeypatch.setattr(server.policy, "resolve_port", lambda port: board["port"])
    monkeypatch.setattr(server.policy, "check_readable", lambda port: None)
    monkeypatch.setattr(server, "enumerate_boards", lambda: [board])
    monkeypatch.setattr(server.sessions, "by_port", lambda port: None)
    monkeypatch.setattr(
        server.flash, "upload_sketch",
        lambda *a, **k: {"ok": True, "port": board["port"], "fqbn": fqbn, "sketch_dir": "/s/x", "artifact_digest": "d"},
    )
    return server.upload_sketch("/s/x", board["port"], fqbn, "token", confirm=True)


def test_fingerprinted_upload_needs_the_opt_in(monkeypatch, tmp_path):
    cache = fingerprint.Cache()
    cache.store(BOARD, fingerprint.analyse(ESP32_BOOT))
    monkeypatch.setattr(server, "fingerprints", cache)

    refused = _upload(monkeypatch, tmp_path, BOARD, Config(allow_flash=True, sketch_roots=("/s",)))
    assert refused["error"]["code"] == errors.UNKNOWN_BOARD
    assert "allow_fingerprinted" in refused["error"]["hint"]

    config = Config(allow_flash=True, sketch_roots=("/s",), allow_fingerprinted=True)
    allowed = _upload(monkeypatch, tmp_path, BOARD, config)
    assert allowed["ok"] is True
    assert allowed["identified_by"] == {"fingerprint": "ESP32"}
    assert "upload_identity_fingerprint" in audit.log_path().read_text()

    wrong_chip = _upload(monkeypatch, tmp_path, BOARD, config, fqbn="esp32:esp32:esp32s3")
    assert wrong_chip["error"]["code"] == errors.UNKNOWN_BOARD


def test_opt_in_without_a_fingerprint_still_refuses(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "fingerprints", fingerprint.Cache())
    config = Config(allow_flash=True, sketch_roots=("/s",), allow_fingerprinted=True)
    assert _upload(monkeypatch, tmp_path, BOARD, config)["error"]["code"] == errors.UNKNOWN_BOARD
