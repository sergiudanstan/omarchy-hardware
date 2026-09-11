"""Parser tests for pinctrl / raspi-gpio output.

These samples are real output formats taken from Raspberry Pi documentation and
forums, not invented. The original regex only handled the `a3 pu` shape and
silently dropped every other line, which would have made GPIO misreport pin state
on an actual Pi -- parsing is the one part of the SSH path testable without one.
"""

import pytest

from omarchy_hardware.gpio_ssh import _parse_pins

PINCTRL_SAMPLES = [
    ("0: a3    pu | hi // ID_SDA/GPIO0 = SDA0", 0, "a3", "pu", None, 1),
    ("26: ip    -- | lo // GPIO26 = input", 26, "ip", None, None, 0),
    ("26: op -- -- | lo // GPIO26 = output", 26, "op", None, None, 0),
    ("6: op dl pu | lo // GPIO6 = output", 6, "op", "pu", "dl", 0),
    ("17: op dh | hi // GPIO17 = output", 17, "op", None, "dh", 1),
]


@pytest.mark.parametrize("line,bcm,mode,pull,drive,level", PINCTRL_SAMPLES)
def test_pinctrl_line_parses(line, bcm, mode, pull, drive, level):
    pins = _parse_pins("pinctrl", line)
    assert len(pins) == 1, f"failed to parse: {line!r}"
    pin = pins[0]
    assert (pin["bcm"], pin["mode"], pin["pull"], pin["drive"], pin["level"]) == (bcm, mode, pull, drive, level)


def test_pinctrl_parses_a_full_listing():
    pins = _parse_pins("pinctrl", "\n".join(s[0] for s in PINCTRL_SAMPLES))
    assert len(pins) == len(PINCTRL_SAMPLES)


@pytest.mark.parametrize(
    "line,bcm,mode,level",
    [
        ("GPIO 17: level=0 fsel=0 func=INPUT", 17, "ip", 0),
        ("GPIO 17: level=1 fsel=1 func=OUTPUT", 17, "op", 1),
        ("GPIO 2: level=1 fsel=4 alt=0 func=SDA1", 2, "sda1", 1),
    ],
)
def test_raspi_gpio_line_parses(line, bcm, mode, level):
    pins = _parse_pins("raspi-gpio", line)
    assert len(pins) == 1, f"failed to parse: {line!r}"
    assert (pins[0]["bcm"], pins[0]["mode"], pins[0]["level"]) == (bcm, mode, level)


def test_both_backends_return_the_same_shape():
    a = _parse_pins("pinctrl", "17: op dh | hi // GPIO17 = output")[0]
    b = _parse_pins("raspi-gpio", "GPIO 17: level=1 fsel=1 func=OUTPUT")[0]
    assert set(a) == set(b)


@pytest.mark.parametrize("junk", ["", "garbage", "Header line", "17: malformed"])
def test_unparseable_lines_are_skipped_not_crashed(junk):
    assert _parse_pins("pinctrl", junk) == []
    assert _parse_pins("raspi-gpio", junk) == []
