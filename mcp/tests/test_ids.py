from omarchy_hardware.ids import identify, identify_nucleo


def test_official_uno_is_flashable():
    info = identify("2341", "0043")
    assert info.board_type == "arduino_uno"
    assert info.fqbn == "arduino:avr:uno"


def test_pico_w_has_its_own_fqbn():
    info = identify("2e8a", "0009")
    assert info.board_type == "rp2040"
    assert info.fqbn == "rp2040:rp2040:rpipicow"


def test_seeed_xiao_samd_is_named():
    info = identify("2886", "802f")
    assert info.board_type == "seeed_xiao"
    assert info.fqbn is not None


def test_teensy_vendor_is_unknown_not_flashable():
    info = identify("16c0", "0483")
    assert info.board_type == "unknown"
    assert info.fqbn is None
    assert "Teensy" in info.friendly_name


def test_ch340_stays_unknown():
    info = identify("1a86", "7523")
    assert info.board_type == "unknown"
    assert info.fqbn is None


def test_uno_r4_wifi_is_flashable():
    info = identify("2341", "1002")
    assert info.board_type == "arduino_uno_r4_wifi"
    assert info.fqbn == "arduino:renesas_uno:unor4wifi"


def test_nano_esp32_is_flashable():
    info = identify("2341", "0070")
    assert info.board_type == "arduino_nano_esp32"
    assert info.fqbn == "arduino:esp32:nano_nora"


def test_pico_2_bootsel_has_uf2_fqbn():
    info = identify("2e8a", "000f")
    assert info.board_type == "rp2350_bootloader"
    assert info.fqbn == "rp2040:rp2040:rpipico2"


def test_debug_probe_is_not_a_flashable_pico():
    info = identify("2e8a", "000c")
    assert info.board_type == "unknown"
    assert info.friendly_name == "Raspberry Pi Debug Probe"
    assert info.fqbn is None


def test_pico_hid_cdc_composite_is_flashable():
    info = identify("2e8a", "000b")
    assert info.board_type == "rp2040"
    assert info.fqbn == "rp2040:rp2040:rpipico"


def test_pico_bootsel_has_uf2_fqbn():
    info = identify("2e8a", "0003")
    assert info.board_type == "rp2040_bootloader"
    assert info.fqbn == "rp2040:rp2040:rpipico"


def test_adafruit_feather_m4_is_flashable():
    info = identify("239a", "8022")
    assert info.board_type == "adafruit_feather_m4"
    assert info.fqbn == "adafruit:samd:adafruit_feather_m4"


def test_seeed_xiao_rp2040_is_flashable():
    info = identify("2886", "8030")
    assert info.board_type == "seeed_xiao_rp2040"
    assert info.fqbn == "rp2040:rp2040:seeed_xiao_rp2040"


def test_sparkfun_promicro_is_flashable():
    info = identify("1b4f", "9206")
    assert info.board_type == "sparkfun_promicro"
    assert info.fqbn == "SparkFun:avr:promicro"


def test_stlink_does_not_claim_a_specific_stm32_board():
    info = identify("0483", "374b")
    assert info.board_type == "unknown"
    assert info.fqbn is None


def test_microbit_version_is_not_assumed_from_usb_id():
    info = identify("0d28", "0204")
    assert info.board_type == "unknown"
    assert info.fqbn is None


def test_stm32_usb_cdc_is_named_but_not_flashable():
    info = identify("0483", "5740")
    assert "STM32" in info.friendly_name
    assert info.fqbn is None


def test_nucleo_label_names_board_and_fqbn():
    info = identify_nucleo("NOD_F411RE")
    assert info.board_type == "stm32_nucleo"
    assert info.friendly_name == "STM32 Nucleo-F411RE"
    assert info.fqbn == "STMicroelectronics:stm32:Nucleo_64:pnum=NUCLEO_F411RE"


def test_unlisted_nucleo_label_is_named_without_guessing_fqbn():
    info = identify_nucleo("NOD_U575ZI")
    assert info.board_type == "unknown"
    assert info.friendly_name == "STM32 Nucleo-U575ZI"
    assert info.fqbn is None


def test_non_nucleo_labels_are_ignored():
    assert identify_nucleo(None) is None
    assert identify_nucleo("DIS_F407VG") is None
    assert identify_nucleo("NOD_") is None
