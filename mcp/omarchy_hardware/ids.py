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
    ("2341", "1002"): BoardInfo("arduino_uno_r4_wifi", "Arduino UNO R4 WiFi", "arduino:renesas_uno:unor4wifi", 115200),
    ("2341", "0070"): BoardInfo("arduino_nano_esp32", "Arduino Nano ESP32", "arduino:esp32:nano_nora", 115200),
    ("2341", "0266"): BoardInfo("arduino_giga", "Arduino GIGA R1 WiFi", "arduino:mbed_giga:giga", 115200),
    ("2341", "025b"): BoardInfo("arduino_portenta_h7", "Arduino Portenta H7", "arduino:mbed_portenta:envie_m7", 115200),
    ("2341", "804f"): BoardInfo("arduino_mkrzero", "Arduino MKR ZERO", "arduino:samd:mkrzero", 9600),
    ("2341", "8050"): BoardInfo("arduino_mkrfox1200", "Arduino MKR FOX 1200", "arduino:samd:mkrfox1200", 9600),
    ("2341", "8052"): BoardInfo("arduino_mkrgsm1400", "Arduino MKR GSM 1400", "arduino:samd:mkrgsm1400", 9600),
    ("2341", "8053"): BoardInfo("arduino_mkrwan1300", "Arduino MKR WAN 1300", "arduino:samd:mkrwan1300", 9600),
    ("2341", "8055"): BoardInfo("arduino_mkrnb1500", "Arduino MKR NB 1500", "arduino:samd:mkrnb1500", 9600),
    ("2341", "8056"): BoardInfo("arduino_mkrvidor4000", "Arduino MKR Vidor 4000", "arduino:samd:mkrvidor4000", 9600),
    ("2341", "8059"): BoardInfo("arduino_mkrwan1310", "Arduino MKR WAN 1310", "arduino:samd:mkrwan1310", 9600),
    ("2e8a", "0005"): BoardInfo("rp2040", "Raspberry Pi Pico", "rp2040:rp2040:rpipico", 115200),
    ("2e8a", "000a"): BoardInfo("rp2040", "Raspberry Pi Pico", "rp2040:rp2040:rpipico", 115200),
    ("2e8a", "0009"): BoardInfo("rp2040", "Raspberry Pi Pico W", "rp2040:rp2040:rpipicow", 115200),
    ("2e8a", "000f"): BoardInfo("rp2350", "Raspberry Pi Pico 2", "rp2040:rp2040:rpipico2", 115200),
    ("2e8a", "0003"): BoardInfo("rp2040_bootloader", "Raspberry Pi Pico (BOOTSEL)", None, 115200),
    ("303a", "0002"): BoardInfo("esp32", "ESP32-S2", "esp32:esp32:esp32s2", 115200),
    ("239a", "800b"): BoardInfo(
        "adafruit_feather_m0", "Adafruit Feather M0", "adafruit:samd:adafruit_feather_m0", 115200
    ),
    ("239a", "8022"): BoardInfo(
        "adafruit_feather_m4", "Adafruit Feather M4 Express", "adafruit:samd:adafruit_feather_m4", 115200
    ),
    ("239a", "80f3"): BoardInfo(
        "adafruit_feather_rp2040", "Adafruit Feather RP2040", "rp2040:rp2040:adafruit_feather", 115200
    ),
    ("239a", "80f7"): BoardInfo("adafruit_qtpy_rp2040", "Adafruit QT Py RP2040", "rp2040:rp2040:adafruit_qtpy", 115200),
    ("239a", "80cb"): BoardInfo("adafruit_qtpy_m0", "Adafruit QT Py SAMD21", "adafruit:samd:adafruit_qtpy_m0", 115200),
    ("239a", "801e"): BoardInfo(
        "adafruit_trinket_m0", "Adafruit Trinket M0", "adafruit:samd:adafruit_trinket_m0", 115200
    ),
    ("239a", "8018"): BoardInfo(
        "adafruit_circuitplayground_m0",
        "Adafruit Circuit Playground Express",
        "adafruit:samd:adafruit_circuitplayground_m0",
        115200,
    ),
    ("239a", "8111"): BoardInfo(
        "adafruit_feather_esp32s3", "Adafruit Feather ESP32-S3", "esp32:esp32:adafruit_feather_esp32s3", 115200
    ),
    ("239a", "8115"): BoardInfo(
        "adafruit_qtpy_esp32s3", "Adafruit QT Py ESP32-S3", "esp32:esp32:adafruit_qtpy_esp32s3", 115200
    ),
    ("2886", "802f"): BoardInfo("seeed_xiao", "Seeed XIAO SAMD21", "Seeeduino:samd:seeed_XIAO_m0", 115200),
    ("2886", "8030"): BoardInfo("seeed_xiao_rp2040", "Seeed XIAO RP2040", "rp2040:rp2040:seeed_xiao_rp2040", 115200),
    ("2886", "8029"): BoardInfo("seeed_xiao_nrf52840", "Seeed XIAO nRF52840", "Seeeduino:nrf52:xiaoblesense", 115200),
    ("2886", "802d"): BoardInfo(
        "seeed_wio_terminal", "Seeed Wio Terminal", "Seeeduino:samd:seeed_wio_terminal", 115200
    ),
    ("1b4f", "9206"): BoardInfo("sparkfun_promicro", "SparkFun Pro Micro 5V", "SparkFun:avr:promicro", 9600),
    ("1b4f", "9204"): BoardInfo("sparkfun_promicro", "SparkFun Pro Micro 3.3V", "SparkFun:avr:promicro", 9600),
    ("1b4f", "0026"): BoardInfo(
        "sparkfun_promicro_rp2040", "SparkFun Pro Micro RP2040", "rp2040:rp2040:sparkfun_promicrorp2040", 115200
    ),
    ("0483", "374b"): BoardInfo("unknown", "STM32 ST-LINK V2-1 (board not identified)", None, 115200),
    ("0483", "374e"): BoardInfo("unknown", "STM32 ST-LINK V3 (board not identified)", None, 115200),
    ("0d28", "0204"): BoardInfo("unknown", "BBC micro:bit (version not identified)", None, 115200),
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
    "0483": "STMicroelectronics",
    "0d28": "BBC micro:bit",
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
