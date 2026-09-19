import pytest

from omarchy_hardware import board_profiles, errors, server, wiring


def _check(profile_id, connections):
    return wiring.check(board_profiles.export(profile_id), connections)


def _messages(result, severity):
    return [f["message"] for f in result["findings"] if f["severity"] == severity]


def test_clean_uno_weather_station_passes():
    result = _check("arduino_uno_r3", [
        {"pin": "A4", "part": "BME280", "role": "i2c_sda", "part_voltage": 5, "i2c_address": "0x76", "pull_up": True},
        {"pin": "A5", "part": "BME280", "role": "i2c_scl", "part_voltage": 5, "i2c_address": "0x76", "pull_up": True},
        {"pin": "5V", "part": "BME280", "role": "power"},
        {"pin": "GND", "part": "BME280", "role": "ground"},
        {"pin": "D9", "part": "status LED", "role": "digital_out", "load": "led", "current_ma": 10},
    ])
    assert result["verdict"] == "pass", result["findings"]


def test_esp32_flash_pin_and_strapping_pin():
    result = _check("esp32_devkitc", [
        {"pin": "GPIO6", "part": "LED", "role": "digital_out"},
        {"pin": "GPIO12", "part": "button", "role": "digital_in"},
    ])
    assert result["verdict"] == "fail"
    assert any("GPIO6 is reserved" in m for m in _messages(result, "error"))
    assert any("GPIO12 needs care" in m and "flash voltage" in m for m in _messages(result, "warning"))


def test_five_volt_part_on_esp32_needs_a_level_shifter():
    echo = {"pin": "GPIO4", "part": "HC-SR04 echo", "role": "digital_in", "part_voltage": 5}
    result = _check("esp32_devkitc", [echo])
    assert any("level shifter" in m for m in _messages(result, "error"))


def test_nucleo_five_volt_is_a_warning_because_some_pins_are_tolerant():
    result = _check("stm32_nucleo64_f4", [{"pin": "D2", "part": "5 V sensor", "role": "digital_in", "part_voltage": 5}])
    assert result["verdict"] == "check"
    assert any("Only some pins are 5 V tolerant" in m for m in _messages(result, "warning"))


def test_five_volt_board_driving_a_three_volt_part():
    result = _check("arduino_uno_r3", [{"pin": "D11", "part": "SD card", "role": "spi_mosi", "part_voltage": 3.3}])
    assert any("drives 5 V into SD card" in m for m in _messages(result, "error"))


def test_three_volt_i2c_on_five_volt_board_is_a_warning():
    result = _check("arduino_uno_r3", [
        {"pin": "A4", "part": "BME280", "role": "i2c_sda", "part_voltage": 3.3, "pull_up": True},
        {"pin": "A5", "part": "BME280", "role": "i2c_scl", "part_voltage": 3.3, "pull_up": True},
    ])
    assert result["verdict"] == "check"
    assert any("open-drain" in m for m in _messages(result, "warning"))


def test_relay_without_a_driver_fails_and_with_one_gets_advice():
    bare = _check("arduino_uno_r3", [{"pin": "D7", "part": "relay", "role": "digital_out", "load": "relay",
                                       "current_ma": 70}])
    assert any("Never drive a relay from a pin" in m for m in _messages(bare, "error"))
    driven = _check("arduino_uno_r3", [{"pin": "D7", "part": "relay module", "role": "digital_out", "load": "relay",
                                         "driver": True, "current_ma": 70}])
    assert driven["verdict"] == "pass"
    assert any("flyback diode" in m for m in _messages(driven, "info"))


def test_current_limits_per_pin_and_total():
    over = _check("raspberry_pi_pico", [{"pin": "GP15", "part": "bright LED", "role": "digital_out", "current_ma": 20}])
    assert any("absolute maximum" in m for m in _messages(over, "error"))
    high = _check("raspberry_pi_pico", [{"pin": "GP15", "part": "LED", "role": "digital_out", "current_ma": 8}])
    assert any("recommended" in m for m in _messages(high, "warning"))
    many = _check("arduino_uno_r3", [
        {"pin": f"D{n}", "part": f"LED {n}", "role": "digital_out", "current_ma": 20} for n in range(2, 13)
    ])
    assert any("in total" in m for m in _messages(many, "error"))


def test_capabilities():
    result = _check("arduino_uno_r3", [
        {"pin": "D4", "part": "servo", "role": "pwm", "load": "servo"},
        {"pin": "D7", "part": "pot", "role": "analog_in"},
    ])
    errors_ = _messages(result, "error")
    assert any("D4 cannot do pwm" in m for m in errors_) and any("D7 cannot do analog_in" in m for m in errors_)


def test_input_only_pins_cannot_drive():
    result = _check("esp32_devkitc", [{"pin": "GPIO34", "part": "LED", "role": "digital_out"}])
    assert any("input-only" in m for m in _messages(result, "error"))
    ok_in = _check("esp32_devkitc", [{"pin": "GPIO34", "part": "LDR", "role": "analog_in"}])
    assert ok_in["verdict"] == "pass"


def test_non_default_bus_pin_is_a_warning():
    result = _check("esp32_devkitc", [
        {"pin": "GPIO25", "part": "OLED", "role": "i2c_sda", "pull_up": True},
        {"pin": "GPIO26", "part": "OLED", "role": "i2c_scl", "pull_up": True},
    ])
    assert result["verdict"] == "check"
    assert all("not the default" in m for m in _messages(result, "warning"))


def test_i2c_bus_rules():
    result = _check("arduino_uno_r3", [
        {"pin": "A4", "part": "OLED", "role": "i2c_sda", "i2c_address": "0x3c"},
        {"pin": "A4", "part": "second OLED", "role": "i2c_sda", "i2c_address": "0x3C"},
    ])
    errors_ = _messages(result, "error")
    assert any("no SCL line" in m for m in errors_)
    assert any("share I2C address 0x3c" in m for m in errors_)
    assert any("pull-ups" in m for m in _messages(result, "warning"))


def test_pin_conflicts_and_supply_pins():
    result = _check("arduino_uno_r3", [
        {"pin": "D5", "part": "LED", "role": "digital_out"},
        {"pin": "D5", "part": "button", "role": "digital_in"},
        {"pin": "5V", "part": "sensor", "role": "digital_in"},
        {"pin": "D6", "part": "fan", "role": "power"},
    ])
    errors_ = _messages(result, "error")
    assert any("D5 is used for" in m for m in errors_)
    assert any("5V is a supply pin" in m for m in errors_)
    assert any("D6 is a signal pin" in m for m in errors_)


def test_unknown_pin_and_pi_header_aliases():
    bad = _check("arduino_uno_r3", [{"pin": "D99", "part": "x", "role": "digital_out"}])
    assert any("D99 is not a pin" in m for m in _messages(bad, "error"))
    pi = _check("raspberry_pi_40pin", [
        {"pin": "PIN11", "part": "LED", "role": "digital_out", "current_ma": 5},
        {"pin": "PIN27", "part": "LED", "role": "digital_out"},
    ])
    assert any("GPIO0 is reserved" in m for m in _messages(pi, "error"))
    assert not any(f["pin"] == "GPIO17" and f["severity"] == "error" for f in pi["findings"])


def test_nucleo_accepts_mcu_pin_names():
    result = _check("stm32_nucleo64_f4", [{"pin": "PA5", "part": "LED", "role": "digital_out", "current_ma": 5}])
    assert result["verdict"] == "pass"


@pytest.mark.parametrize(
    "connections",
    [
        [],
        "D3",
        [{"pin": "D3", "role": "laser"}],
        [{"pin": "D3", "role": "pwm", "load": "rocket"}],
        [{"pin": "D3", "role": "pwm", "current_ma": -1}],
        [{"pin": "D3", "role": "pwm", "part_voltage": True}],
        [{"pin": "D3", "role": "pwm", "extra": 1}],
        [{"pin": "D3", "role": "pwm", "driver": "yes"}],
        [{"pin": "D3", "role": "i2c_sda", "i2c_address": "0x80"}],
        [{"pin": "D3", "role": "pwm"}] * 81,
    ],
)
def test_malformed_plans_are_rejected(connections):
    with pytest.raises(errors.ToolError) as caught:
        _check("arduino_uno_r3", connections)
    assert caught.value.code == errors.INVALID_ARGUMENT


def test_tool_resolves_the_board_like_board_profile():
    result = server.wiring_check([{"pin": "D3", "part": "LED", "role": "pwm"}], fqbn="arduino:avr:uno")
    assert result["ok"] is True and result["board"] == "arduino_uno_r3" and result["verdict"] == "pass"
    assert server.wiring_check([{"pin": "D3", "role": "pwm"}])["error"]["code"] == errors.INVALID_ARGUMENT
    missing = server.wiring_check([{"pin": "D3", "role": "pwm"}], fqbn="arduino:avr:leonardo")
    assert missing["error"]["code"] == errors.PROFILE_NOT_FOUND
