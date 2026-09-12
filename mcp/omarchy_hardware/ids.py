"""USB VID/PID identification for development boards and USB-serial bridge chips."""

from typing import NamedTuple


class BoardInfo(NamedTuple):
    board_type: str
    friendly_name: str
    fqbn: str | None
    baud: int


# Exact (vid, pid) matches for boards we can name confidently.
BOARDS: dict[tuple[str, str], BoardInfo] = {
    ("2341", "0043"): BoardInfo("arduino_uno", "Arduino Uno", "arduino:avr:uno", 9600),
    ("2341", "0001"): BoardInfo("arduino_uno", "Arduino Uno", "arduino:avr:uno", 9600),
    ("2a03", "0043"): BoardInfo("arduino_uno", "Arduino Uno (clone)", "arduino:avr:uno", 9600),
    ("2341", "0010"): BoardInfo("arduino_mega", "Arduino Mega 2560", "arduino:avr:mega", 9600),
    ("2341", "0042"): BoardInfo("arduino_mega", "Arduino Mega 2560 R3", "arduino:avr:mega", 9600),
    ("2341", "8036"): BoardInfo("arduino_leonardo", "Arduino Leonardo", "arduino:avr:leonardo", 9600),
    ("2341", "8037"): BoardInfo("arduino_micro", "Arduino Micro", "arduino:avr:micro", 9600),
    ("2341", "0058"): BoardInfo("arduino_nano_every", "Arduino Nano Every", "arduino:megaavr:nona4809", 9600),
    ("2341", "8057"): BoardInfo("arduino_nano_33_iot", "Arduino Nano 33 IoT", "arduino:samd:nano_33_iot", 9600),
    ("2341", "805a"): BoardInfo("arduino_nano_33_ble", "Arduino Nano 33 BLE", "arduino:mbed_nano:nano33ble", 9600),
    ("2341", "003d"): BoardInfo("arduino_due", "Arduino Due", "arduino:sam:arduino_due_x", 115200),
    ("2341", "804d"): BoardInfo("arduino_zero", "Arduino Zero", "arduino:samd:arduino_zero_native", 9600),
    ("2341", "804e"): BoardInfo("arduino_mkr1000", "Arduino MKR1000", "arduino:samd:mkr1000", 9600),
    ("2341", "8054"): BoardInfo("arduino_mkrwifi1010", "Arduino MKR WiFi 1010", "arduino:samd:mkrwifi1010", 9600),
    ("2341", "0069"): BoardInfo("arduino_uno_r4", "Arduino UNO R4 Minima", "arduino:renesas_uno:minima", 115200),
    ("2e8a", "0005"): BoardInfo("rp2040", "Raspberry Pi Pico", "rp2040:rp2040:rpipico", 115200),
    ("2e8a", "000a"): BoardInfo("rp2040", "Raspberry Pi Pico", "rp2040:rp2040:rpipico", 115200),
    ("2e8a", "0009"): BoardInfo("rp2040", "Raspberry Pi Pico W", "rp2040:rp2040:rpipicow", 115200),
    ("2e8a", "0003"): BoardInfo("rp2040_bootloader", "Raspberry Pi Pico (BOOTSEL)", None, 115200),
    ("303a", "0002"): BoardInfo("esp32", "ESP32-S2", "esp32:esp32:esp32s2", 115200),
    ("2886", "802f"): BoardInfo("seeed_xiao", "Seeed XIAO SAMD21", "Seeeduino:samd:seeed_XIAO_m0", 115200),
}

# USB-serial bridge chips and ambiguous IDs. These identify the adapter, not a
# flashable board, so board_type stays unknown and fqbn is None.
CHIPS: dict[tuple[str, str], str] = {
    ("303a", "1001"): "Espressif USB (S2/S3 ambiguous)",
    ("1a86", "7523"): "CH340 USB-serial",
    ("1a86", "5523"): "CH341 USB-serial",
    ("1a86", "55d4"): "CH9102 USB-serial",
    ("0403", "6001"): "FTDI FT232R",
    ("0403", "6010"): "FTDI FT2232",
    ("0403", "6015"): "FTDI FT231X",
    ("10c4", "ea60"): "Silicon Labs CP210x",
    ("10c4", "ea70"): "Silicon Labs CP2105",
    ("067b", "2303"): "Prolific PL2303",
}

# Vendors whose boards we recognise generically when the exact PID is unlisted.
VENDORS: dict[str, str] = {
    "2341": "Arduino",
    "2a03": "Arduino",
    "2e8a": "Raspberry Pi",
    "303a": "Espressif",
    "239a": "Adafruit",
    "1b4f": "SparkFun",
    "2886": "Seeed Studio",
    "16c0": "PJRC Teensy",
}


def identify(vid: str, pid: str) -> BoardInfo:
    vid, pid = vid.lower(), pid.lower()

    if (vid, pid) in BOARDS:
        return BOARDS[(vid, pid)]

    if (vid, pid) in CHIPS:
        return BoardInfo("unknown", CHIPS[(vid, pid)], None, 115200)

    if vid in VENDORS:
        return BoardInfo("unknown", f"{VENDORS[vid]} device {vid}:{pid}", None, 115200)

    return BoardInfo("unknown", f"Serial device {vid}:{pid}", None, 115200)
