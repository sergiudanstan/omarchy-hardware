import asyncio
import json
import shutil
import tomllib
from pathlib import Path

import pytest

from omarchy_hardware import board_profiles, errors, policy, server
from omarchy_hardware.ids import BOARDS, RP2_APP_BOARDS, nucleo_boards


@pytest.fixture
def profile_dir(tmp_path, monkeypatch):
    """A private copy of the shipped profiles that a test can break."""
    target = tmp_path / "profiles"
    shutil.copytree(board_profiles.PROFILE_DIR, target)
    monkeypatch.setattr(board_profiles, "PROFILE_DIR", target)
    board_profiles._load_all.cache_clear()
    yield target
    board_profiles._load_all.cache_clear()


def _pin(profile_id, name):
    return next(pin for pin in board_profiles.all_profiles()[profile_id]["pins"] if pin["name"] == name)


def _rewrite(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_every_shipped_profile_loads():
    ids = {entry["id"] for entry in board_profiles.index()}
    assert ids == {
        "arduino_uno_r3",
        "arduino_nano",
        "arduino_mega_2560",
        "esp32_devkitc",
        "raspberry_pi_pico",
        "raspberry_pi_pico_w",
        "stm32_nucleo64_f4",
        "raspberry_pi_40pin",
    }


@pytest.mark.parametrize(
    ("fqbn", "profile_id"),
    [
        ("arduino:avr:uno", "arduino_uno_r3"),
        ("arduino:avr:mega", "arduino_mega_2560"),
        ("arduino:avr:mega:cpu=atmega2560", "arduino_mega_2560"),
        ("arduino:avr:nano:cpu=atmega328old", "arduino_nano"),
        ("esp32:esp32:esp32", "esp32_devkitc"),
        ("rp2040:rp2040:rpipicow", "raspberry_pi_pico_w"),
        ("STMicroelectronics:stm32:Nucleo_64:pnum=NUCLEO_F411RE", "stm32_nucleo64_f4"),
        ("STMicroelectronics:stm32:Nucleo_64:upload_method=MassStorage,pnum=NUCLEO_F446RE", "stm32_nucleo64_f4"),
    ],
)
def test_fqbn_matches_profile_with_extra_options(fqbn, profile_id):
    assert board_profiles.profile_id_for_fqbn(fqbn) == profile_id


@pytest.mark.parametrize(
    "fqbn",
    [
        None,
        "",
        "uno",
        "arduino:avr",
        "arduino:avr:leonardo",
        # Same board id, different part: a Nucleo-L476RG pinout is not the F4 one.
        "STMicroelectronics:stm32:Nucleo_64:pnum=NUCLEO_L476RG",
        "STMicroelectronics:stm32:Nucleo_64",
    ],
)
def test_fqbn_without_a_profile_is_not_guessed(fqbn):
    assert board_profiles.profile_id_for_fqbn(fqbn) is None


def test_usb_identified_boards_reach_their_profiles():
    detected = {info.fqbn for info in [*BOARDS.values(), *RP2_APP_BOARDS.values(), *nucleo_boards()] if info.fqbn}
    for fqbn, profile_id in {
        "arduino:avr:uno": "arduino_uno_r3",
        "arduino:avr:mega": "arduino_mega_2560",
        "rp2040:rp2040:rpipico": "raspberry_pi_pico",
        "rp2040:rp2040:rpipicow": "raspberry_pi_pico_w",
        "STMicroelectronics:stm32:Nucleo_64:pnum=NUCLEO_F401RE": "stm32_nucleo64_f4",
        "STMicroelectronics:stm32:Nucleo_64:pnum=NUCLEO_F411RE": "stm32_nucleo64_f4",
        "STMicroelectronics:stm32:Nucleo_64:pnum=NUCLEO_F446RE": "stm32_nucleo64_f4",
    }.items():
        assert fqbn in detected
        assert board_profiles.profile_id_for_fqbn(fqbn) == profile_id


def test_esp32_flash_and_strapping_pins_are_flagged():
    for gpio in range(6, 12):
        assert "reserved" in _pin("esp32_devkitc", f"GPIO{gpio}")
    for gpio in (0, 2, 5, 12, 15):
        assert "Strapping" in _pin("esp32_devkitc", f"GPIO{gpio}")["caution"]
    for gpio in (34, 35, 36, 39):
        pin = _pin("esp32_devkitc", f"GPIO{gpio}")
        assert "input_only" in pin["caps"]
        assert "pwm" not in pin["caps"] and "digital" not in pin["caps"]
    assert _pin("esp32_devkitc", "GPIO25")["adc"] == "ADC2_CH8"


def test_three_volt_boards_do_not_claim_five_volt_tolerance():
    for profile in board_profiles.all_profiles().values():
        if profile["logic_voltage"] == 5.0:
            assert profile["five_volt_tolerant"] == "all"
        else:
            assert profile["five_volt_tolerant"] in {"none", "some"}
    assert board_profiles.all_profiles()["esp32_devkitc"]["five_volt_tolerant"] == "none"


def test_uno_serial_pins_need_caution_and_i2c_is_on_a4_a5():
    assert "caution" in _pin("arduino_uno_r3", "D0")
    assert "caution" in _pin("arduino_uno_r3", "D1")
    assert "i2c_sda" in _pin("arduino_uno_r3", "A4")["caps"]
    assert "i2c_scl" in _pin("arduino_uno_r3", "A5")["caps"]
    pwm = {pin["gpio"] for pin in board_profiles.all_profiles()["arduino_uno_r3"]["pins"] if "pwm" in pin["caps"]}
    assert pwm == {3, 5, 6, 9, 10, 11}


def test_pico_w_wireless_pins_are_reserved():
    for gpio in (23, 24, 25, 29):
        assert "reserved" in _pin("raspberry_pi_pico_w", f"GP{gpio}")
    assert "led" in _pin("raspberry_pi_pico", "GP25")["caps"]


def test_nucleo_serial_pins_belong_to_the_stlink():
    assert "ST-LINK" in _pin("stm32_nucleo64_f4", "D0")["reserved"]
    assert _pin("stm32_nucleo64_f4", "D13")["mcu_pin"] == "PA5"
    # examples/stm32-servo-sweep drives a servo from D3.
    assert "pwm" in _pin("stm32_nucleo64_f4", "D3")["caps"]


def test_pi_header_maps_physical_positions_to_bcm_numbers():
    pins = board_profiles.all_profiles()["raspberry_pi_40pin"]["pins"]
    assert [pin["physical"] for pin in pins] == list(range(1, 41))
    by_physical = {pin["physical"]: pin for pin in pins}
    assert by_physical[11]["gpio"] == 17
    assert by_physical[12]["gpio"] == 18
    assert by_physical[2]["caps"] == ["power"]
    assert {pin["gpio"] for pin in pins if "pwm" in pin["caps"]} == {12, 13, 18, 19}
    assert "reserved" in by_physical[27] and "reserved" in by_physical[28]
    bcm = sorted(pin["gpio"] for pin in pins if "gpio" in pin)
    assert bcm == list(range(28))


def test_export_lists_pins_to_avoid_and_the_data_status():
    exported = board_profiles.export("esp32_devkitc")
    assert {entry["name"] for entry in exported["reserved_pins"]} == {f"GPIO{n}" for n in range(6, 12)}
    assert any(entry["name"] == "GPIO12" for entry in exported["caution_pins"])
    assert "not physically validated" in exported["data_status"]


def test_export_of_unknown_profile_is_a_tool_error():
    with pytest.raises(errors.ToolError) as caught:
        board_profiles.export("../../etc/passwd")
    assert caught.value.code == errors.PROFILE_NOT_FOUND


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ('mcu = "ATmega328P, 16 MHz"', 'mcu = "ATmega328P, 16 MHz"\ncolour = "teal"', "unknown keys"),
        ('caps = ["digital", "interrupt"]', 'caps = ["digital", "laser"]', "unknown caps"),
        ('id = "arduino_uno_r3"', 'id = "arduino_uno"', "does not match the file name"),
        ("gpio = 4\n", "gpio = 3\n", "duplicate gpio"),
        ("logic_voltage = 5.0", "logic_voltage = 12.0", "logic_voltage"),
        ('five_volt_tolerant = "all"', 'five_volt_tolerant = "mostly"', "five_volt_tolerant"),
        ("per_pin_recommended = 20", "per_pin_recommended = 60", "per_pin_recommended"),
        (
            'caution = "USB serial RX. Anything connected here interferes with Serial and with uploads."',
            'caution = "x"\nreserved = "y"',
            "not both",
        ),
    ],
)
def test_malformed_profiles_fail_loudly(profile_dir, old, new, message):
    _rewrite(profile_dir / "arduino_uno_r3.toml", old, new)
    with pytest.raises(board_profiles.ProfileError, match=message):
        board_profiles.all_profiles()


def test_two_profiles_cannot_claim_one_fqbn(profile_dir):
    _rewrite(profile_dir / "arduino_nano.toml", 'fqbns = ["arduino:avr:nano"]', 'fqbns = ["arduino:avr:uno"]')
    with pytest.raises(board_profiles.ProfileError, match="claimed by both"):
        board_profiles.all_profiles()


def test_profiles_ship_as_package_data():
    pyproject = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    assert "profiles/*.toml" in pyproject["tool"]["setuptools"]["package-data"]["omarchy_hardware"]


# --------------------------------------------------------------------------- MCP surface


def _fake_board(monkeypatch, **fields):
    board = {"port": "/dev/ttyACM0", "friendly_name": "Arduino Uno", "suggested_fqbn": "arduino:avr:uno", **fields}
    monkeypatch.setattr(policy, "resolve_port", lambda port: "/dev/ttyACM0")
    monkeypatch.setattr(server, "enumerate_boards", lambda: [board])
    return board


def test_board_profile_without_arguments_lists_profiles():
    result = server.board_profile()
    assert result["ok"] is True
    assert {"id": "arduino_uno_r3", "name": "Arduino Uno R3", "family": "microcontroller",
            "fqbns": ["arduino:avr:uno"]} in result["profiles"]


def test_board_profile_by_port_uses_the_detected_fqbn(monkeypatch):
    _fake_board(monkeypatch)
    result = server.board_profile(port="/dev/ttyACM0")
    assert result["ok"] is True
    assert result["profile"]["id"] == "arduino_uno_r3"
    assert result["matched_fqbn"] == "arduino:avr:uno"


def test_board_profile_by_port_refuses_to_guess_an_unidentified_adapter(monkeypatch):
    _fake_board(monkeypatch, friendly_name="CH340 USB-serial", suggested_fqbn=None)
    result = server.board_profile(port="/dev/ttyACM0")
    assert result["ok"] is False
    assert result["error"]["code"] == errors.PROFILE_NOT_FOUND
    assert "fqbn=" in result["error"]["hint"]


def test_board_profile_rejects_more_than_one_selector():
    result = server.board_profile(fqbn="arduino:avr:uno", profile_id="arduino_uno_r3")
    assert result["error"]["code"] == errors.INVALID_ARGUMENT


def test_board_profile_by_id_and_by_unmatched_fqbn():
    assert server.board_profile(profile_id="raspberry_pi_40pin")["profile"]["family"] == "raspberry_pi"
    missing = server.board_profile(fqbn="arduino:avr:leonardo")
    assert missing["error"]["code"] == errors.PROFILE_NOT_FOUND


def test_describe_board_names_the_profile(monkeypatch):
    _fake_board(monkeypatch)
    assert server.describe_board("/dev/ttyACM0")["board"]["profile_id"] == "arduino_uno_r3"


def test_profiles_are_readable_as_mcp_resources():
    templates = asyncio.run(server.mcp.list_resource_templates())
    assert [t.uri_template for t in templates] == ["hardware://board-profiles/{profile_id}"]
    contents = asyncio.run(server.mcp.read_resource("hardware://board-profiles/stm32_nucleo64_f4"))
    assert json.loads(contents[0].content)["id"] == "stm32_nucleo64_f4"
    listing = asyncio.run(server.mcp.read_resource("hardware://board-profiles"))
    assert len(json.loads(listing[0].content)) == len(board_profiles.all_profiles())
