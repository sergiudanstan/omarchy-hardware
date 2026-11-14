import asyncio
import copy
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
        "esp32s3_devkitc1",
        "lilygo_t_qt_pro",
        "lilygo_t_display_s3",
        "lilygo_t_display",
        "jczn_2432s028r",
        "m5stack_atoms3",
        "m5stack_core2",
        "adafruit_feather_esp32s3_tft",
        "heltec_wifi_kit_32_v3",
        "waveshare_esp32s3_touch_lcd_128",
        "m5stack_stickc_plus2",
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
        ("esp32:esp32:esp32s3", "esp32s3_devkitc1"),
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
    package_data = pyproject["tool"]["setuptools"]["package-data"]["omarchy_hardware"]
    assert "peripherals.toml" in package_data
    assert "displays.toml" in package_data


# --------------------------------------------------------------------------- MCP surface


def _fake_board(monkeypatch, **fields):
    board = {"port": "/dev/ttyACM0", "friendly_name": "Arduino Uno", "suggested_fqbn": "arduino:avr:uno", **fields}
    monkeypatch.setattr(policy, "resolve_port", lambda port: "/dev/ttyACM0")
    monkeypatch.setattr(server, "enumerate_boards", lambda: [board])
    return board


def test_board_profile_without_arguments_lists_profiles():
    result = server.board_profile()
    assert result["ok"] is True
    assert any(e["id"] == "arduino_uno_r3" for e in result["profiles"])
    # New fields in index.
    for entry in result["profiles"]:
        assert "chip" in entry
        assert "display" in entry


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
    assert {t.uri_template for t in templates} == {
        "hardware://board-profiles/{profile_id}", "hardware://devices/{profile_id}",
    }
    contents = asyncio.run(server.mcp.read_resource("hardware://board-profiles/stm32_nucleo64_f4"))
    assert json.loads(contents[0].content)["id"] == "stm32_nucleo64_f4"
    listing = asyncio.run(server.mcp.read_resource("hardware://board-profiles"))
    assert len(json.loads(listing[0].content)) == len(board_profiles.all_profiles())


# --------------------------------------------------------------------------- Schema validation: peripherals


def test_valid_display_peripheral_loads():
    """The T-QT Pro has a valid display peripheral."""
    p = board_profiles.all_profiles()["lilygo_t_qt_pro"]
    displays = [per for per in p["peripherals"] if per["kind"] == "display"]
    assert len(displays) == 1
    d = displays[0]
    assert d["controller"] == "GC9107"
    assert d["width"] == 128 and d["height"] == 128
    assert d["interface"] == "spi"
    assert d["color"] == "rgb565"
    assert d["backlight_active"] == "low"


def test_peripheral_unknown_key_fails(profile_dir):
    _rewrite(
        profile_dir / "lilygo_t_qt_pro.toml",
        'name = "0.85 inch IPS LCD"',
        'name = "0.85 inch IPS LCD"\nbrightness = 100',
    )
    with pytest.raises(board_profiles.ProfileError, match="unknown keys"):
        board_profiles.all_profiles()


def test_peripheral_bad_interface_enum_fails(profile_dir):
    _rewrite(profile_dir / "lilygo_t_qt_pro.toml", 'interface = "spi"', 'interface = "serial"')
    with pytest.raises(board_profiles.ProfileError, match="interface must be one of"):
        board_profiles.all_profiles()


def test_peripheral_bad_color_enum_fails(profile_dir):
    _rewrite(profile_dir / "lilygo_t_qt_pro.toml", 'color = "rgb565"', 'color = "rgb888"')
    with pytest.raises(board_profiles.ProfileError, match="color must be one of"):
        board_profiles.all_profiles()


def test_peripheral_gpio_not_in_pins_fails(profile_dir):
    # Change a display pin to a GPIO not in the profile's pin list.
    _rewrite(profile_dir / "lilygo_t_qt_pro.toml", "dc = 6", "dc = 99")
    with pytest.raises(board_profiles.ProfileError, match="GPIO 99 is not a"):
        board_profiles.all_profiles()


def test_peripheral_pin_without_reserved_or_caution_fails(profile_dir):
    # Remove the reserved text from GPIO1 (display RST pin).
    _rewrite(
        profile_dir / "lilygo_t_qt_pro.toml",
        'reserved = "Display RST pin."',
        'note = "Display RST pin."',
    )
    with pytest.raises(board_profiles.ProfileError, match="no reserved or caution text"):
        board_profiles.all_profiles()


def test_peripheral_double_gpio_claim_non_shared_fails(profile_dir):
    # Add a second peripheral claiming the same GPIO as a non-shared role.
    extra = '\n\n[[peripherals]]\nkind = "led"\nname = "extra"\npins = { output = 1 }\n'
    path = profile_dir / "lilygo_t_qt_pro.toml"
    path.write_text(path.read_text() + extra)
    with pytest.raises(board_profiles.ProfileError, match="GPIO 1 is claimed by"):
        board_profiles.all_profiles()


def test_peripheral_unknown_kind_fails(profile_dir):
    _rewrite(profile_dir / "lilygo_t_qt_pro.toml", 'kind = "display"', 'kind = "camera"')
    with pytest.raises(board_profiles.ProfileError, match="kind must be one of"):
        board_profiles.all_profiles()


def test_peripheral_bad_button_enum_fails(profile_dir):
    _rewrite(profile_dir / "lilygo_t_qt_pro.toml", 'active = "low"', 'active = "toggle"', )
    with pytest.raises(board_profiles.ProfileError, match="active must be one of"):
        board_profiles.all_profiles()


def test_peripheral_unknown_pin_role_fails(profile_dir):
    _rewrite(profile_dir / "lilygo_t_qt_pro.toml", "sense = 4", "voltage = 4")
    with pytest.raises(board_profiles.ProfileError, match="unknown pin role"):
        board_profiles.all_profiles()


# --------------------------------------------------------------------------- Schema validation: identity


def test_identity_bad_chip_fails(profile_dir):
    _rewrite(profile_dir / "esp32s3_devkitc1.toml", 'chip = "ESP32-S3"', 'chip = "ESP32-S4"')
    with pytest.raises(board_profiles.ProfileError, match="chip must be one of"):
        board_profiles.all_profiles()


def test_identity_bad_usb_vid_fails(profile_dir):
    _rewrite(profile_dir / "esp32s3_devkitc1.toml", 'vid = "303a"', 'vid = "ZZZZ"')
    with pytest.raises(board_profiles.ProfileError, match="vid must be 4 lower-case hex"):
        board_profiles.all_profiles()


def test_identity_unknown_key_fails(profile_dir):
    _rewrite(
        profile_dir / "esp32s3_devkitc1.toml",
        'vendor = "Espressif"',
        'vendor = "Espressif"\nbrand = "ESPressif"',
    )
    with pytest.raises(board_profiles.ProfileError, match="unknown keys"):
        board_profiles.all_profiles()


# --------------------------------------------------------------------------- Schema validation: build


def test_build_fqbn_bad_parts_fails(profile_dir):
    _rewrite(profile_dir / "lilygo_t_qt_pro.toml", 'fqbn = "esp32:esp32:esp32s3"', 'fqbn = "esp32:esp32"')
    with pytest.raises(board_profiles.ProfileError, match="exactly three colon-separated"):
        board_profiles.all_profiles()


def test_build_option_bad_chars_fails(profile_dir):
    _rewrite(
        profile_dir / "lilygo_t_qt_pro.toml",
        'FlashSize = "4M"',
        '"Flash Size" = "4M"',
    )
    with pytest.raises(board_profiles.ProfileError):
        board_profiles.all_profiles()


# --------------------------------------------------------------------------- Schema validation: libraries


def test_library_bad_for_fails(profile_dir):
    _rewrite(profile_dir / "lilygo_t_qt_pro.toml", 'for = "display"', 'for = "screen"')
    with pytest.raises(board_profiles.ProfileError, match="must be one of"):
        board_profiles.all_profiles()


# --------------------------------------------------------------------------- Schema validation: validation


def test_maintainer_verified_without_note_fails(profile_dir):
    path = profile_dir / "lilygo_t_qt_pro.toml"
    text = path.read_text()
    path.write_text("\n".join(line for line in text.splitlines() if not line.startswith('note = "Maintainer tested')))
    with pytest.raises(board_profiles.ProfileError, match="maintainer_verified requires a note"):
        board_profiles.all_profiles()


def test_validation_bad_status_fails(profile_dir):
    _rewrite(
        profile_dir / "lilygo_t_qt_pro.toml",
        'status = "maintainer_verified"',
        'status = "user_verified"',
    )
    with pytest.raises(board_profiles.ProfileError, match="status must be one of"):
        board_profiles.all_profiles()


# --------------------------------------------------------------------------- fqbns=[] without [build]


def test_empty_fqbns_without_build_section_fails(profile_dir):
    """A microcontroller profile with fqbns=[] must have a [build] section."""
    # Remove the [build] section from the T-QT Pro (which has fqbns=[]).
    path = profile_dir / "lilygo_t_qt_pro.toml"
    text = path.read_text()
    # Remove lines from [build] to the next section.
    lines = text.split("\n")
    new_lines = []
    skip = False
    for line in lines:
        if line.strip().startswith("[build]"):
            skip = True
            continue
        if skip and line.strip().startswith("[") and not line.strip().startswith("[build"):
            skip = False
        if not skip:
            new_lines.append(line)
    path.write_text("\n".join(new_lines))
    with pytest.raises(board_profiles.ProfileError, match="at least one FQBN or a .build. section"):
        board_profiles.all_profiles()


# --------------------------------------------------------------------------- Reverse lookup: fqbns=[] profiles


def test_fqbn_for_generic_s3_never_returns_t_qt_pro():
    """The T-QT Pro has fqbns=[], so profile_id_for_fqbn must not return it.

    Even with the exact build options, the FQBN should resolve to the generic
    ESP32-S3 profile (esp32s3_devkitc1), because the T-QT does not claim the FQBN.
    """
    assert board_profiles.profile_id_for_fqbn("esp32:esp32:esp32s3") == "esp32s3_devkitc1"
    assert board_profiles.profile_id_for_fqbn(
        "esp32:esp32:esp32s3:FlashSize=4M,PSRAM=enabled,CDCOnBoot=cdc"
    ) == "esp32s3_devkitc1"


# --------------------------------------------------------------------------- ESP32-S3 chip-level consistency


# Source: https://www.espressif.com/sites/default/files/documentation/esp32-s3_datasheet_en.pdf
# v2.2: sections 2.3 (pin functions), 2.6 (flash/PSRAM mapping), 3 (strapping pins).
_ESP32S3_ADC = {
    # GPIO -> ADC channel (ADC1: GPIO1-10, ADC2: GPIO11-20)
    1: "ADC1_CH0", 2: "ADC1_CH1", 3: "ADC1_CH2", 4: "ADC1_CH3", 5: "ADC1_CH4",
    6: "ADC1_CH5", 7: "ADC1_CH6", 8: "ADC1_CH7", 9: "ADC1_CH8", 10: "ADC1_CH9",
    11: "ADC2_CH0", 12: "ADC2_CH1", 13: "ADC2_CH2", 14: "ADC2_CH3", 15: "ADC2_CH4",
    16: "ADC2_CH5", 17: "ADC2_CH6", 18: "ADC2_CH7", 19: "ADC2_CH8", 20: "ADC2_CH9",
}
_ESP32S3_STRAPPING = {0, 3, 45, 46}
_ESP32S3_USB = {19, 20}
_ESP32S3_FLASH_RESERVED = set(range(26, 33))  # GPIO26-32


@pytest.mark.parametrize("profile_id", [
    pid for pid, p in board_profiles.all_profiles().items()
    if p.get("identity", {}).get("chip") == "ESP32-S3"
])
def test_esp32s3_chip_consistency(profile_id):
    """Every ESP32-S3 profile's pins must agree with the datasheet tables."""
    profile = board_profiles.all_profiles()[profile_id]
    for pin in profile["pins"]:
        if "gpio" not in pin:
            continue
        gpio = pin["gpio"]

        # ADC channel check.
        if gpio in _ESP32S3_ADC:
            assert "analog_in" in pin["caps"]
            assert pin.get("adc") == _ESP32S3_ADC[gpio], (
                f"{profile_id} GPIO{gpio}: expected {_ESP32S3_ADC[gpio]}, got {pin.get('adc')}"
            )
        else:
            assert "analog_in" not in pin["caps"]
            assert "adc" not in pin, (
                f"{profile_id} GPIO{gpio}: should not have ADC"
            )

        # Strapping pin check.
        if gpio in _ESP32S3_STRAPPING:
            assert "strapping" in (pin.get("caution", "") + pin.get("reserved", "")).lower(), (
                f"{profile_id} GPIO{gpio}: strapping pin needs an explicit warning"
            )

        # USB pin check.
        if gpio in _ESP32S3_USB:
            assert "USB" in pin.get("caution", "") + pin.get("reserved", ""), (
                f"{profile_id} GPIO{gpio}: USB pin needs an explicit warning"
            )

        # Flash reserved check (GPIO26-32 must be reserved on every S3 board).
        if gpio in _ESP32S3_FLASH_RESERVED:
            assert "reserved" in pin, (
                f"{profile_id} GPIO{gpio}: SPI flash pin must be reserved"
            )


# --------------------------------------------------------------------------- New ESP32-S3 profiles


def test_esp32s3_devkitc1_flash_and_strapping_pins():
    for gpio in range(26, 38):
        assert "reserved" in _pin("esp32s3_devkitc1", f"GPIO{gpio}")
    for gpio in (0, 3, 45, 46):
        pin = _pin("esp32s3_devkitc1", f"GPIO{gpio}")
        assert "caution" in pin
    for gpio in (19, 20):
        pin = _pin("esp32s3_devkitc1", f"GPIO{gpio}")
        assert "USB" in pin["caution"]
    # RGB LED.
    assert "led" in _pin("esp32s3_devkitc1", "GPIO48")["caps"]
    assert "LED" in _pin("esp32s3_devkitc1", "GPIO38")["caution"]


def test_esp32s3_devkitc1_identity():
    p = board_profiles.all_profiles()["esp32s3_devkitc1"]
    identity = p["identity"]
    assert identity["vendor"] == "Espressif"
    assert identity["chip"] == "ESP32-S3"
    assert identity["usb"] == [{"vid": "303a", "pid": "1001"}]
    assert identity["usb_unique"] is False


@pytest.mark.parametrize(
    ("profile_id", "fqbn", "display"),
    [
        ("lilygo_t_display_s3", "esp32:esp32:lilygo_t_display_s3", ("ST7789", 170, 320)),
        ("lilygo_t_display", "esp32:esp32:lilygo_t_display", ("ST7789", 135, 240)),
        ("jczn_2432s028r", "esp32:esp32:jczn_2432s028r", ("ILI9341", 320, 240)),
        ("m5stack_atoms3", "esp32:esp32:m5stack_atoms3", ("GC9107", 128, 128)),
        ("m5stack_core2", "esp32:esp32:m5stack_core2", ("ILI9342C", 320, 240)),
    ],
)
def test_new_display_profiles_document_board_fqbn_and_display(profile_id, fqbn, display):
    profile = board_profiles.all_profiles()[profile_id]
    assert profile["fqbns"] == [fqbn]
    assert profile["build"]["fqbn"] == fqbn
    screen = next(peripheral for peripheral in profile["peripherals"] if peripheral["kind"] == "display")
    assert (screen["controller"], screen["width"], screen["height"]) == display
    assert profile["validation"]["status"] == "documented"


def test_cyd_display_touch_and_sd_wiring():
    peripherals = board_profiles.all_profiles()["jczn_2432s028r"]["peripherals"]
    display, touch, *rest = peripherals
    assert display["pins"] == {"mosi": 13, "miso": 12, "sclk": 14, "cs": 15, "dc": 2, "backlight": 21}
    assert touch["controller"] == "XPT2046"
    assert any(peripheral["kind"] == "sd_card" for peripheral in rest)


def test_atoms3_display_and_onboard_imu_wiring():
    profile = board_profiles.all_profiles()["m5stack_atoms3"]
    display = next(peripheral for peripheral in profile["peripherals"] if peripheral["kind"] == "display")
    imu = next(peripheral for peripheral in profile["peripherals"] if peripheral["kind"] == "sensor")
    assert display["pins"] == {"mosi": 21, "sclk": 17, "cs": 15, "dc": 33, "rst": 34}
    assert imu["pins"] == {"sda": 38, "scl": 39}


def test_core2_build_enables_documented_psram_and_pmics_control_lcd():
    profile = board_profiles.all_profiles()["m5stack_core2"]
    assert profile["build"]["options"] == {"PSRAM": "enabled"}
    display = next(peripheral for peripheral in profile["peripherals"] if peripheral["kind"] == "display")
    assert "AXP192" in display["note"]
    assert display["pins"] == {"mosi": 23, "miso": 38, "sclk": 18, "cs": 5, "dc": 15}


def test_lilygo_t_qt_pro_display_and_build():
    p = board_profiles.all_profiles()["lilygo_t_qt_pro"]
    assert p["fqbns"] == []
    build = p["build"]
    assert build["fqbn"] == "esp32:esp32:esp32s3"
    assert build["options"] == {"FlashSize": "4M", "PSRAM": "enabled", "CDCOnBoot": "cdc"}

    displays = [per for per in p["peripherals"] if per["kind"] == "display"]
    assert len(displays) == 1
    d = displays[0]
    assert d["pins"] == {"mosi": 2, "sclk": 3, "cs": 5, "dc": 6, "rst": 1, "backlight": 10}
    assert d["backlight_active"] == "low"

    buttons = [per for per in p["peripherals"] if per["kind"] == "button"]
    assert len(buttons) == 2
    assert {b["pins"]["input"] for b in buttons} == {0, 47}


def test_lilygo_t_qt_pro_validation():
    p = board_profiles.all_profiles()["lilygo_t_qt_pro"]
    v = p["validation"]
    assert v["status"] == "maintainer_verified"
    assert "2026-11-12" in v["note"]
    assert "GC9107" in v["note"]


# --- export / index / build_fqbn / display_summary / candidates


def test_export_includes_build_and_peripherals():
    exported = board_profiles.export("lilygo_t_qt_pro")
    assert "fqbn_full" in exported["build"]
    assert exported["build"]["fqbn_full"] == "esp32:esp32:esp32s3:CDCOnBoot=cdc,FlashSize=4M,PSRAM=enabled"
    assert "peripherals" in exported
    assert "onboard_pins" in exported
    assert exported["onboard_pins"]["6"] == "display dc"
    # Data status should mention maintainer, not "not physically validated".
    assert "not physically validated" not in exported["data_status"]
    assert "Maintainer" in exported["data_status"]


def test_export_documented_profile_says_not_validated():
    exported = board_profiles.export("esp32s3_devkitc1")
    assert "not physically validated" in exported["data_status"]


def test_index_has_chip_and_display():
    entries = {e["id"]: e for e in board_profiles.index()}
    assert entries["lilygo_t_qt_pro"]["chip"] == "ESP32-S3"
    assert entries["lilygo_t_qt_pro"]["display"] == "GC9107 128x128"
    assert entries["esp32s3_devkitc1"]["chip"] == "ESP32-S3"
    assert entries["esp32s3_devkitc1"]["display"] is None
    assert entries["esp32_devkitc"]["chip"] is None
    assert entries["esp32_devkitc"]["display"] is None


def test_build_fqbn_returns_full_fqbn():
    assert board_profiles.build_fqbn("lilygo_t_qt_pro") == (
        "esp32:esp32:esp32s3:CDCOnBoot=cdc,FlashSize=4M,PSRAM=enabled"
    )


def test_build_fqbn_returns_none_without_build():
    assert board_profiles.build_fqbn("esp32_devkitc") is None


def test_display_summary_returns_string_or_none():
    assert board_profiles.display_summary("lilygo_t_qt_pro") == "GC9107 128x128"
    assert board_profiles.display_summary("esp32_devkitc") is None


def test_candidates_by_usb_id():
    results = board_profiles.candidates("303a", "1001")
    assert "esp32s3_devkitc1" in results
    assert "lilygo_t_qt_pro" in results
    # Profiles without identity should not appear.
    assert "esp32_devkitc" not in results


def test_candidates_filters_by_chip():
    results = board_profiles.candidates("303a", "1001", chip="ESP32-S3")
    assert "esp32s3_devkitc1" in results
    assert "lilygo_t_qt_pro" in results
    # Non-existent chip.
    assert board_profiles.candidates("303a", "1001", chip="ESP32-C3") == []


def test_candidates_filters_by_flash():
    results_4 = board_profiles.candidates("303a", "1001", flash_mb=4)
    assert "lilygo_t_qt_pro" in results_4
    results_8 = board_profiles.candidates("303a", "1001", flash_mb=8)
    assert "lilygo_t_qt_pro" not in results_8
    # esp32s3_devkitc1 has no flash_mb, so it matches any.
    assert "esp32s3_devkitc1" in results_8


def test_candidates_filters_by_psram():
    results_2 = board_profiles.candidates("303a", "1001", psram_mb=2)
    assert "lilygo_t_qt_pro" in results_2
    results_8 = board_profiles.candidates("303a", "1001", psram_mb=8)
    assert "lilygo_t_qt_pro" not in results_8


def test_candidates_returns_sorted():
    results = board_profiles.candidates("303a", "1001")
    assert results == sorted(results)


def test_candidates_no_match():
    assert board_profiles.candidates("dead", "beef") == []


# --------------------------------------------------------------------------- Wiring check with on-board display pin


def test_wiring_check_warns_on_display_reserved_pin():
    """Using a pin that is reserved for the on-board display should produce an error."""
    from omarchy_hardware import wiring
    profile = board_profiles.export("lilygo_t_qt_pro")
    # Try to use GPIO6 (display DC) for a digital output.
    result = wiring.check(profile, [{"pin": "GPIO6", "part": "LED", "role": "digital_out"}])
    assert result["verdict"] == "fail"
    errors_found = [f for f in result["findings"] if f["severity"] == "error"]
    assert any("reserved" in f["message"].lower() or "display" in f["message"].lower() for f in errors_found)


# --------------------------------------------------------------------------- FQBN existence test


def test_fqbn_claims_exist_in_installed_cores():
    """Every FQBN that any profile claims (fqbns) or builds with (build.fqbn)
    for the esp32 arch must appear in the installed core's board list."""
    fqbn_file = Path(__file__).resolve().parent / "data" / "esp32_core_fqbns.txt"
    known = set()
    for line in fqbn_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            known.add(line)
    assert len(known) > 100, "FQBN list looks too short"

    for profile in board_profiles.all_profiles().values():
        for fqbn in profile["fqbns"]:
            base = fqbn.split(":")[1] if ":" in fqbn else ""
            if base == "esp32":
                board_fqbn = ":".join(fqbn.split(":")[:3])
                assert board_fqbn in known, f"{profile['id']} claims {fqbn}, but {board_fqbn} not in installed cores"
        build = profile.get("build")
        if build:
            fqbn = build["fqbn"]
            base = fqbn.split(":")[1] if ":" in fqbn else ""
            if base == "esp32":
                board_fqbn = ":".join(fqbn.split(":")[:3])
                assert board_fqbn in known, (
                    f"{profile['id']} builds with {fqbn}, but {board_fqbn} not in installed cores"
                )


# --------------------------------------------------------------------------- Packaged data test


def test_new_profiles_are_packaged():
    """The new TOML files must be found by the packaged-data glob."""
    toml_files = {p.stem for p in board_profiles.PROFILE_DIR.glob("*.toml")}
    assert "esp32s3_devkitc1" in toml_files
    assert "lilygo_t_qt_pro" in toml_files


@pytest.mark.parametrize(("section", "entry"), [
    ("identity", {"usb_unique": 1}),
    ("identity", {"usb": ["303a:1001"]}),
    ("identity", {"usb": [{"vid": "303a\n", "pid": "1001"}]}),
    ("identity", {"usb": [{"vid": "303a", "pid": "1001", "extra": True}]}),
    ("identity", {"flash_mb": [True]}),
    ("identity", {"psram_mb": [-1]}),
    ("build", {"fqbn": "esp32:esp32:esp32s3", "extra": "x"}),
    ("build", {"fqbn": "esp32:esp32:esp32s3:PSRAM=enabled"}),
    ("build", {"fqbn": "esp32::esp32s3"}),
    ("build", {"fqbn": "esp32:esp32:esp32s3\n"}),
    ("build", {"fqbn": "esp32:esp32:esp32s3", "options": {"FlashSize": 4}}),
    ("build", {"fqbn": "esp32:esp32:esp32s3", "options": {"FlashSize": "4M,PSRAM=enabled"}}),
    ("libraries", [{"name": "GFX", "for": "display", "unknown": "x"}]),
    ("libraries", [{"name": "GFX", "for": "display", "setup": [1]}]),
    ("libraries", ["GFX"]),
    ("validation", {"status": "documented", "unknown": "x"}),
    ("validation", {"status": "maintainer_verified", "note": "  "}),
])
def test_new_sections_reject_malformed_data(section, entry):
    profile = copy.deepcopy(board_profiles.all_profiles()["lilygo_t_qt_pro"])
    profile[section] = entry
    with pytest.raises(board_profiles.ProfileError):
        board_profiles.validate(profile, profile["id"])


@pytest.mark.parametrize(("key", "value"), [
    ("width", 0), ("height", -1), ("width", True), ("height", "128"),
    ("pins", {"dc": True}), ("pins", {"dc": "6"}), ("pins", {}),
    ("pins", {"mosi": 2, "sclk": 2}), ("backlight_active", "inverted"),
])
def test_display_rejects_malformed_dimensions_and_pins(key, value):
    profile = copy.deepcopy(board_profiles.all_profiles()["lilygo_t_qt_pro"])
    profile["peripherals"][0][key] = value
    with pytest.raises(board_profiles.ProfileError):
        board_profiles.validate(profile, profile["id"])


def test_backlight_requires_polarity_and_pin_annotation_requires_text():
    profile = copy.deepcopy(board_profiles.all_profiles()["lilygo_t_qt_pro"])
    del profile["peripherals"][0]["backlight_active"]
    with pytest.raises(board_profiles.ProfileError, match="backlight_active is required"):
        board_profiles.validate(profile, profile["id"])
    profile["peripherals"][0]["backlight_active"] = "low"
    next(p for p in profile["pins"] if p.get("gpio") == 6)["reserved"] = "  "
    with pytest.raises(board_profiles.ProfileError, match="no reserved or caution text"):
        board_profiles.validate(profile, profile["id"])


@pytest.mark.parametrize(("kind", "extra"), [
    ("display", {"controller": "test", "interface": "rgb", "width": 1, "height": 1, "color": "rgb565"}),
    ("touch", {"controller": "test", "interface": "i2c"}),
    ("button", {"active": "low", "pull": "internal_up"}),
    ("led", {}), ("rgb_led", {}), ("battery", {}),
    ("sd_card", {"interface": "sdmmc"}), ("sensor", {"interface": "onewire"}),
    ("audio", {"interface": "i2s"}), ("radio", {"interface": "spi"}), ("power", {}),
])
def test_every_peripheral_kind_has_a_strict_schema(kind, extra):
    profile = copy.deepcopy(board_profiles.all_profiles()["lilygo_t_qt_pro"])
    role = sorted(board_profiles._PIN_ROLES_BY_KIND[kind])[0]
    peripheral = {"kind": kind, "name": "test", "pins": {role: 6}, **extra}
    if role == "backlight":
        peripheral["backlight_active"] = "low"
    profile["peripherals"] = [peripheral]
    board_profiles.validate(profile, profile["id"])
    if "interface" in peripheral:
        interface = peripheral["interface"]
        peripheral["interface"] = "invalid"
        with pytest.raises(board_profiles.ProfileError, match="interface must be one of"):
            board_profiles.validate(profile, profile["id"])
        peripheral["interface"] = interface
    peripheral["typo"] = "x"
    with pytest.raises(board_profiles.ProfileError, match="unknown keys"):
        board_profiles.validate(profile, profile["id"])


@pytest.mark.parametrize(("role", "gpio", "valid"), [
    ("mosi", 2, True), ("sclk", 3, True), ("miso", 2, False), ("cs", 5, False),
])
def test_shared_spi_roles_must_agree(role, gpio, valid, monkeypatch):
    profile = copy.deepcopy(board_profiles.all_profiles()["lilygo_t_qt_pro"])
    profile["peripherals"].append({"kind": "sd_card", "name": "test SD", "interface": "spi", "pins": {role: gpio}})
    if valid:
        board_profiles.validate(profile, profile["id"])
        monkeypatch.setattr(board_profiles, "_load_all", lambda: {profile["id"]: profile})
        assert board_profiles.export(profile["id"])["onboard_pins"][str(gpio)] == f"display {role}; sd_card {role}"
    else:
        with pytest.raises(board_profiles.ProfileError, match="is claimed by"):
            board_profiles.validate(profile, profile["id"])


def test_i2c_peripherals_can_share_matching_bus_roles():
    profile = copy.deepcopy(board_profiles.all_profiles()["lilygo_t_qt_pro"])
    profile["peripherals"] = [
        {"kind": "sensor", "name": "one", "interface": "i2c", "pins": {"sda": 2, "scl": 3}},
        {"kind": "touch", "name": "two", "controller": "test", "interface": "i2c", "pins": {"sda": 2, "scl": 3}},
    ]
    board_profiles.validate(profile, profile["id"])


def test_candidates_empty_psram_means_none_and_missing_sizes_are_unknown(monkeypatch):
    profiles = {
        "none": {"id": "none", "identity": {"usb": [{"vid": "303a", "pid": "1001"}], "psram_mb": []}},
        "unknown": {"id": "unknown", "identity": {"usb": [{"vid": "303a", "pid": "1001"}]}},
        "no_identity": {"id": "no_identity"},
    }
    monkeypatch.setattr(board_profiles, "_load_all", lambda: profiles)
    assert board_profiles.candidates("303a", "1001", psram_mb=0) == ["none", "unknown"]
    assert board_profiles.candidates("303a", "1001", psram_mb=2) == ["unknown"]
    assert board_profiles.candidates("303a", "1001", chip="ESP32-S3", flash_mb=4) == ["none", "unknown"]


def test_export_omits_absent_sections():
    profile = board_profiles.export("arduino_uno_r3")
    assert not {"identity", "build", "peripherals", "libraries", "validation", "onboard_pins"} & profile.keys()


@pytest.mark.parametrize("lookup", [board_profiles.build_fqbn, board_profiles.display_summary])
def test_new_helpers_report_unknown_profile(lookup):
    with pytest.raises(errors.ToolError) as caught:
        lookup("does_not_exist")
    assert caught.value.code == errors.PROFILE_NOT_FOUND


def test_wiring_check_cautions_for_onboard_button():
    from omarchy_hardware import wiring
    result = wiring.check(board_profiles.export("lilygo_t_qt_pro"), [
        {"pin": "GPIO47", "part": "LED", "role": "digital_out"},
    ])
    assert result["verdict"] == "check"
    assert any(f["severity"] == "warning" and "button" in f["message"] for f in result["findings"])


def test_server_exports_new_profile_sections():
    profile = server.board_profile(profile_id="lilygo_t_qt_pro")["profile"]
    assert profile == board_profiles.export("lilygo_t_qt_pro")
    assert profile["build"]["fqbn_full"] == board_profiles.build_fqbn("lilygo_t_qt_pro")


def test_esp32s3_touch_capability_only_on_touch_channels():
    """ESP32-S3 datasheet, analog functions: TOUCH1-TOUCH14 are GPIO1-GPIO14 and nothing else."""
    for entry in board_profiles.index():
        profile = board_profiles.export(entry["id"])
        if profile.get("identity", {}).get("chip") != "ESP32-S3":
            continue
        for pin in profile["pins"]:
            if "touch" in pin["caps"]:
                assert 1 <= pin.get("gpio", -1) <= 14, (entry["id"], pin["name"])
