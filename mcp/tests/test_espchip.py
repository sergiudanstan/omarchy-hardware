"""One recorded S3 fixture; every other esptool response here is synthetic."""

import os
import subprocess
from pathlib import Path

import pytest

from omarchy_hardware import errors, espchip, flash

REAL_S3 = (Path(__file__).parent / "fixtures" / "esptool-flash-id-esp32s3-native-usb.txt").read_text()


def response(monkeypatch, text, code=0):
    monkeypatch.setattr(espchip, "_esptool", lambda *a, **kw: subprocess.CompletedProcess([], code, text, ""))


def test_recorded_t_qt_pro_flash_id(monkeypatch):
    # Captured by the maintainer 2026-11-12, esptool v5.6.0; no hardware used here.
    response(monkeypatch, REAL_S3)
    assert espchip.probe("/dev/ttyACM0") == {
        "chip": "ESP32-S3", "chip_detail": "ESP32-S3 (QFN56) (revision v0.2)",
        "mac": "34:b7:da:5a:e4:88", "flash_mb": 4, "psram_mb": 2, "usb_mode": "USB-Serial/JTAG",
    }


@pytest.mark.parametrize("name,family", [(name, name) for name in espchip.ESP_CHIP_FQBNS] + [
    ("ESP32-D0WD-V3", "ESP32"), ("ESP32-PICO-D4", "ESP32"),
])
def test_synthetic_chip_variants(monkeypatch, name, family):
    response(monkeypatch, f"Chip type: {name} (revision v1.0)\nMAC: AA:BB:CC:DD:EE:FF\n")
    result = espchip.probe("/dev/ttyUSB0")
    assert result["chip"] == family
    assert result["mac"] == "aa:bb:cc:dd:ee:ff"
    assert result["psram_mb"] is result["flash_mb"] is result["usb_mode"] is None


@pytest.mark.parametrize("text,code,error", [
    ("Chip type: ESP99\nMAC: aa:bb:cc:dd:ee:ff\n", 0, errors.UNSUPPORTED_OPERATION),
    ("garbage", 0, errors.UNSUPPORTED_OPERATION),
    ("Chip type: ESP32-S3 <instructions>\nMAC: aa:bb:cc:dd:ee:ff\n", 0, errors.UNSUPPORTED_OPERATION),
    ("Chip type: ESP32-S3 " + "a" * 121, 0, errors.UNSUPPORTED_OPERATION),
    (REAL_S3, 1, errors.SERIAL_ERROR),
    ("Chip type: ESP32-S3\nMAC: not-a-mac\n", 0, errors.SERIAL_ERROR),
])
def test_synthetic_invalid_output(monkeypatch, text, code, error):
    response(monkeypatch, text, code)
    with pytest.raises(errors.ToolError) as exc:
        espchip.probe("/dev/ttyACM0")
    assert exc.value.code == error


def test_v5_versions_are_sorted_numerically_not_by_mtime(tmp_path, monkeypatch):
    monkeypatch.delenv("OMARCHY_HARDWARE_ESPTOOL", raising=False)
    monkeypatch.setenv("OMARCHY_HARDWARE_ARDUINO_DATA", str(tmp_path))
    for version in ("5.10.0", "5.9.0", "4.99.0", "garbage"):
        tool = tmp_path / "packages/esp32/tools/esptool_py" / version / "esptool"
        tool.parent.mkdir(parents=True)
        tool.write_text("#!/bin/sh\n")
        tool.chmod(0o700)
        os.utime(tool, (1, 1) if version == "5.10.0" else (999, 999))
    assert "/5.10.0/" in espchip.esptool_path()
    monkeypatch.setenv("OMARCHY_HARDWARE_ARDUINO_DATA", "relative")
    with pytest.raises(errors.ToolError, match="absolute"):
        espchip.esptool_path()


def test_v4_only_is_refused(tmp_path, monkeypatch):
    monkeypatch.delenv("OMARCHY_HARDWARE_ESPTOOL", raising=False)
    monkeypatch.setenv("OMARCHY_HARDWARE_ARDUINO_DATA", str(tmp_path))
    tool = tmp_path / "packages/esp32/tools/esptool_py/4.9.0/esptool"
    tool.parent.mkdir(parents=True)
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o700)
    with pytest.raises(errors.ToolError) as exc:
        espchip.esptool_path()
    assert exc.value.code == errors.TOOL_MISSING


def test_runner_fixed_argv_and_timeout(monkeypatch):
    monkeypatch.setattr(espchip, "esptool_path", lambda: "/trusted/esptool")
    calls = []

    def run(argv, timeout):
        calls.append((argv, timeout))
        return subprocess.CompletedProcess(argv, 0, REAL_S3, "")

    monkeypatch.setattr(flash, "run_group", run)
    espchip.probe("/dev/ttyACM0")
    espchip.identify("/dev/ttyACM0")
    assert calls == [(["/trusted/esptool", "--port", "/dev/ttyACM0", "--baud", "460800", verb], 60)
                     for verb in ("flash-id", "read-mac")]

    def timeout(*a):
        raise subprocess.TimeoutExpired(a[0], 60)

    monkeypatch.setattr(flash, "run_group", timeout)
    with pytest.raises(errors.ToolError) as exc:
        espchip.probe("/dev/ttyACM0")
    assert exc.value.code == errors.SERIAL_ERROR


@pytest.mark.parametrize("stdout,code,expected", [
    ("build.mcu=esp32s3\n", 0, "esp32s3"), ("build.mcu=atmega328p\n", 0, "atmega328p"),
    ("build.mcu=esp32s3\n", 1, None), ("garbage", 0, None),
    ("build.mcu=esp32s3 <untrusted>\n", 0, None),
])
def test_board_mcu_fixed_cli_and_cache(monkeypatch, stdout, code, expected):
    flash.board_mcu.cache_clear()
    calls = []

    def run(argv, timeout):
        calls.append((argv, timeout))
        return subprocess.CompletedProcess(argv, code, stdout, "")

    monkeypatch.setattr(flash, "run_group", run)
    assert flash.board_mcu("esp32:esp32:esp32s3") == expected
    assert flash.board_mcu("esp32:esp32:esp32s3") == expected
    assert calls == [([flash.ARDUINO_CLI, "board", "details", "-b", "esp32:esp32:esp32s3",
                      "--show-properties=unexpanded"], 60)]
    assert os.path.isabs(calls[0][0][0])
    flash.board_mcu.cache_clear()


def test_seven_byte_mac_is_not_a_valid_mac_or_eui64(monkeypatch):
    response(monkeypatch, "Chip type: ESP32-C6\nMAC: aa:bb:cc:dd:ee:ff:00\n")
    with pytest.raises(errors.ToolError) as exc:
        espchip.identify("/dev/ttyACM0")
    assert exc.value.code == errors.SERIAL_ERROR


def test_board_mcu_cli_failure_is_unknown(monkeypatch):
    flash.board_mcu.cache_clear()

    def missing(*args, **kwargs):
        raise errors.ToolError(errors.TOOL_MISSING, "missing arduino-cli")

    monkeypatch.setattr(flash, "_arduino_cli", missing)
    assert flash.board_mcu("esp32:esp32:esp32s3") is None
    flash.board_mcu.cache_clear()
