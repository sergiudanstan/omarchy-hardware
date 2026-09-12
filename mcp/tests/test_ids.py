from omarchy_hardware.ids import identify


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
