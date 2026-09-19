import re
from pathlib import Path

import pytest

from omarchy_hardware import errors, parts, peripherals, server

SKETCH = (Path(peripherals.__file__).resolve().parent / "templates" / "probe" / "omarchy_probe" / "omarchy_probe.ino")


def test_table_loads_and_is_well_formed():
    devices = peripherals.table()
    assert len(devices) >= 30
    assert {d["name"] for d in devices} >= {"BME280", "BMP280", "MPU-6050", "SSD1306 or SH1106 OLED"}


def test_probe_sketch_reads_exactly_the_registers_the_table_needs():
    body = SKETCH.read_text()
    table = body.split("ID_READS[] = {", 1)[1].split("};", 1)[0]
    pairs = {(a.lower(), r.lower()) for a, r in re.findall(r"\{(0x[0-9a-fA-F]{2}),\s*(0x[0-9a-fA-F]{2})\}", table)}
    expected = {(address, register) for address, registers in peripherals.id_reads().items() for register in registers}
    assert pairs == expected


def test_probe_sketch_never_writes_a_data_byte():
    body = SKETCH.read_text()
    # One Wire.write per read: the register pointer, sent with a repeated start.
    assert body.count("Wire.write(") == 1
    assert "Wire.endTransmission(false)" in body


def test_chip_id_confirms_and_rules_out():
    [result] = peripherals.identify([{"a": "0x76", "id": {"0xD0": "0x60"}}])
    assert [c["name"] for c in result["confirmed"]] == ["BME280"]
    assert set(result["ruled_out"]) == {"BMP280", "BME680"}
    assert result["possible"] == [], "address-only guesses are dropped once an ID confirms"


def test_bmp280_samples_and_unknown_ids():
    [bmp] = peripherals.identify([{"a": "0x77", "id": {"0xd0": "0x58"}}])
    assert [c["name"] for c in bmp["confirmed"]] == ["BMP280"]
    [odd] = peripherals.identify([{"a": "0x76", "id": {"0xd0": "0x99"}}])
    assert odd["confirmed"] == []
    assert "TCA9548A I2C multiplexer" in [p["name"] for p in odd["possible"]]


def test_address_only_devices_are_possible():
    [oled] = peripherals.identify(["0x3c"])
    names = [p["name"] for p in oled["possible"]]
    assert "SSD1306 or SH1106 OLED" in names and "PCF8574A I2C LCD backpack or I/O expander" in names


def test_mpu6050_versus_rtc_on_0x68():
    [imu] = peripherals.identify([{"a": "0x68", "id": {"0x75": "0x68", "0x00": "0x12"}}])
    assert [c["name"] for c in imu["confirmed"]] == ["MPU-6050"]
    [rtc] = peripherals.identify([{"a": "0x68", "id": {"0x75": "0x00", "0x00": "0x12"}}])
    assert rtc["confirmed"] == []
    assert [p["name"] for p in rtc["possible"]] == ["DS3231 or DS1307 real-time clock"]


def test_unknown_address_gets_a_note():
    [result] = peripherals.identify(["0x0b"])
    assert result["confirmed"] == [] and result["possible"] == [] and "note" in result


@pytest.mark.parametrize(
    "report",
    [
        "0x76",
        [{"a": "0x78"}],
        [{"a": "76"}],
        [{"a": "0x76; rm -rf"}],
        [{"a": "0x76", "id": ["0xd0"]}],
        [{"a": "0x76", "id": {f"0x{n:02x}": "0x00" for n in range(17)}}],
        ["0x10"] * 129,
        [42],
    ],
)
def test_malformed_reports_are_rejected(report):
    with pytest.raises(errors.ToolError) as caught:
        peripherals.identify(report)
    assert caught.value.code == errors.INVALID_ARGUMENT


def test_garbage_register_values_are_ignored():
    [result] = peripherals.identify([{"a": "0x76", "id": {"0xd0": "<script>", "zz": "0x60"}}])
    assert result["confirmed"] == [] and result["ruled_out"] == []


def test_tool_cross_references_the_parts_inventory(tmp_path, monkeypatch):
    monkeypatch.setattr(parts, "CONFIG_DIR", tmp_path)
    (tmp_path / "parts.toml").write_text('[[part]]\nname = "My BME280 board"\ni2c_address = "0x76"\n')
    result = server.identify_i2c([{"a": "0x76", "id": {"0xd0": "0x60"}}, {"a": "0x3c", "id": {}}])
    assert result["ok"] is True
    first, second = result["devices"]
    assert first["in_parts_inventory"] == ["My BME280 board"]
    assert "in_parts_inventory" not in second
    assert server.identify_i2c("0x76")["error"]["code"] == errors.INVALID_ARGUMENT
