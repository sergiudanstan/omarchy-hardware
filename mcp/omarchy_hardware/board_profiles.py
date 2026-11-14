"""Board profiles: pinout, logic voltage, current limits and pins to avoid.

The profiles are data files shipped with the package (profiles/*.toml), written
from the manufacturer documents listed in each file's `sources`. They exist so a
model reads pin facts instead of recalling them. Loading is strict: an unknown
key or capability is a packaging bug, and fails loudly in the test suite rather
than being passed to the model half-read.

Stdlib-only, like boards.py.
"""

from __future__ import annotations

import functools
import re
import tomllib
from pathlib import Path
from typing import Any

from . import errors
from .errors import ToolError

PROFILE_DIR = Path(__file__).resolve().parent / "profiles"

FAMILIES = frozenset({"microcontroller", "raspberry_pi"})
FIVE_VOLT_TOLERANCE = frozenset({"all", "some", "none"})
LOGIC_VOLTAGES = frozenset({1.8, 3.3, 5.0})
CAPS = frozenset(
    {
        "digital",
        "input_only",
        "analog_in",
        "dac",
        "pwm",
        "touch",
        "interrupt",
        "i2c_sda",
        "i2c_scl",
        "spi_mosi",
        "spi_miso",
        "spi_sck",
        "spi_cs",
        "uart_tx",
        "uart_rx",
        "led",
        "power",
        "ground",
    }
)

_REQUIRED = {
    "id": str,
    "name": str,
    "family": str,
    "mcu": str,
    "fqbns": list,
    "logic_voltage": float,
    "five_volt_tolerant": str,
    "flash_methods": list,
    "sources": list,
    "notes": list,
    "current_ma": dict,
    "pins": list,
}
_OPTIONAL = {"led_builtin": str, "power": list, "identity": dict, "build": dict,
             "peripherals": list, "libraries": list, "validation": dict}
_CURRENT_KEYS = {"per_pin_recommended": int, "per_pin_absolute_max": int, "total_max": int, "note": str}
_PIN_KEYS = {
    "name": str,
    "gpio": int,
    "physical": int,
    "mcu_pin": str,
    "caps": list,
    "adc": str,
    "reserved": str,
    "caution": str,
    "note": str,
}
_POWER_KEYS = {"name": str, "note": str}

# --- Identity section ---
_IDENTITY_KEYS = {
    "vendor": str, "usb": list, "usb_unique": bool, "chip": str,
    "module": str, "flash_mb": list, "psram_mb": list,
}
_IDENTITY_REQUIRED: set[str] = set()  # all optional
_USB_ENTRY_KEYS = {"vid": str, "pid": str}
CHIP_NAMES = frozenset({
    "ESP32", "ESP32-S2", "ESP32-S3", "ESP32-C3", "ESP32-C6", "ESP32-H2",
})
_HEX4 = re.compile(r"^[0-9a-f]{4}$")

# --- Build section ---
_BUILD_KEYS = {"fqbn": str, "options": dict, "note": str}
_BUILD_REQUIRED = {"fqbn"}
# Charset for FQBN option keys and values, matching flash.FQBN_PATTERN.
_FQBN_OPT = re.compile(r"^[A-Za-z0-9_.-]+$")

# --- Peripherals section ---
PERIPHERAL_KINDS = frozenset({
    "display", "touch", "button", "led", "rgb_led", "battery",
    "sd_card", "sensor", "audio", "radio", "power",
})
DISPLAY_INTERFACES = frozenset({"spi", "i2c", "parallel8", "qspi", "rgb"})
DISPLAY_COLORS = frozenset({"rgb565", "mono"})
BACKLIGHT_ACTIVE = frozenset({"low", "high"})
BUTTON_ACTIVE = frozenset({"low", "high"})
PULL_OPTIONS = frozenset({
    "external_up", "external_down", "internal_up", "internal_down", "none",
})

# Per-kind pin role vocabularies.
_DISPLAY_PIN_ROLES = frozenset({
    "mosi", "sclk", "cs", "dc", "rst", "backlight", "miso",
    "sda", "scl",  # I2C displays
    "d0", "d1", "d2", "d3", "d4", "d5", "d6", "d7", "wr", "rd",  # parallel
    "te", "busy",  # tear-effect / busy
    "pclk", "hsync", "vsync", "de",  # RGB timing
    "r0", "r1", "r2", "r3", "r4", "g0", "g1", "g2", "g3", "g4", "g5",
    "b0", "b1", "b2", "b3", "b4",  # RGB565 data
})
_TOUCH_PIN_ROLES = frozenset({"irq", "rst", "cs", "sda", "scl", "mosi", "miso", "sclk"})
_BUTTON_PIN_ROLES = frozenset({"input"})
_LED_PIN_ROLES = frozenset({"output"})
_RGB_LED_PIN_ROLES = frozenset({"data", "clk", "red", "green", "blue"})
_BATTERY_PIN_ROLES = frozenset({"sense", "enable"})
_SD_CARD_PIN_ROLES = frozenset({"mosi", "miso", "sclk", "cs", "cmd", "d0", "d1", "d2", "d3", "clk"})
_SENSOR_PIN_ROLES = frozenset({"sda", "scl", "cs", "mosi", "miso", "sclk", "data", "clk", "irq", "rst", "tx", "rx"})
_AUDIO_PIN_ROLES = frozenset({"bclk", "ws", "data_out", "data_in", "mclk", "sd_mode"})
_RADIO_PIN_ROLES = frozenset({
    "mosi", "miso", "sclk", "cs", "rst", "irq", "dio0", "dio1", "busy", "txen", "rxen",
    "tx", "rx", "cmd", "clk", "d0", "d1", "d2", "d3",
})
_POWER_PERIPH_PIN_ROLES = frozenset({"enable", "sense", "pgood"})

_PIN_ROLES_BY_KIND: dict[str, frozenset[str]] = {
    "display": _DISPLAY_PIN_ROLES,
    "touch": _TOUCH_PIN_ROLES,
    "button": _BUTTON_PIN_ROLES,
    "led": _LED_PIN_ROLES,
    "rgb_led": _RGB_LED_PIN_ROLES,
    "battery": _BATTERY_PIN_ROLES,
    "sd_card": _SD_CARD_PIN_ROLES,
    "sensor": _SENSOR_PIN_ROLES,
    "audio": _AUDIO_PIN_ROLES,
    "radio": _RADIO_PIN_ROLES,
    "power": _POWER_PERIPH_PIN_ROLES,
}

# A shared bus GPIO must have the same role in every peripheral.
_SHARED_ROLES = frozenset({"mosi", "sclk", "miso", "sda", "scl"})
_INTERFACES_BY_KIND = {
    "display": DISPLAY_INTERFACES,
    "touch": frozenset({"spi", "i2c", "analog"}),
    "sd_card": frozenset({"spi", "sdmmc"}),
    "sensor": frozenset({"spi", "i2c", "analog", "digital", "onewire", "uart"}),
    "audio": frozenset({"i2s", "pdm", "analog", "pwm"}),
    "radio": frozenset({"spi", "uart", "sdio"}),
}

# Per-kind required and optional keys (beyond the common ones).
_DISPLAY_KEYS = {
    "kind": str, "name": str, "controller": str, "interface": str,
    "width": int, "height": int, "color": str, "pins": dict,
    "backlight_active": str, "note": str,
}
_DISPLAY_REQUIRED = {"kind", "name", "controller", "interface", "width", "height", "color", "pins"}

_TOUCH_KEYS = {
    "kind": str, "name": str, "controller": str, "interface": str,
    "width": int, "height": int, "pins": dict, "note": str,
}
_TOUCH_REQUIRED = {"kind", "name", "controller", "interface", "pins"}

_BUTTON_KEYS = {
    "kind": str, "name": str, "pins": dict, "active": str, "pull": str, "note": str,
}
_BUTTON_REQUIRED = {"kind", "name", "pins", "active", "pull"}

_LED_KEYS = {"kind": str, "name": str, "pins": dict, "note": str}
_LED_REQUIRED = {"kind", "name", "pins"}

_RGB_LED_KEYS = {"kind": str, "name": str, "pins": dict, "controller": str, "note": str}
_RGB_LED_REQUIRED = {"kind", "name", "pins"}

_BATTERY_KEYS = {"kind": str, "name": str, "pins": dict, "note": str}
_BATTERY_REQUIRED = {"kind", "name", "pins"}

_SD_CARD_KEYS = {"kind": str, "name": str, "interface": str, "pins": dict, "note": str}
_SD_CARD_REQUIRED = {"kind", "name", "pins"}

_SENSOR_KEYS = {
    "kind": str, "name": str, "controller": str, "interface": str,
    "pins": dict, "note": str,
}
_SENSOR_REQUIRED = {"kind", "name", "pins"}

_AUDIO_KEYS = {"kind": str, "name": str, "controller": str, "interface": str, "pins": dict, "note": str}
_AUDIO_REQUIRED = {"kind", "name", "pins"}

_RADIO_KEYS = {"kind": str, "name": str, "controller": str, "interface": str, "pins": dict, "note": str}
_RADIO_REQUIRED = {"kind", "name", "pins"}

_POWER_PERIPH_KEYS = {"kind": str, "name": str, "pins": dict, "note": str}
_POWER_PERIPH_REQUIRED = {"kind", "name"}

_PERIPH_SCHEMA: dict[str, tuple[dict[str, type], set[str]]] = {
    "display": (_DISPLAY_KEYS, _DISPLAY_REQUIRED),
    "touch": (_TOUCH_KEYS, _TOUCH_REQUIRED),
    "button": (_BUTTON_KEYS, _BUTTON_REQUIRED),
    "led": (_LED_KEYS, _LED_REQUIRED),
    "rgb_led": (_RGB_LED_KEYS, _RGB_LED_REQUIRED),
    "battery": (_BATTERY_KEYS, _BATTERY_REQUIRED),
    "sd_card": (_SD_CARD_KEYS, _SD_CARD_REQUIRED),
    "sensor": (_SENSOR_KEYS, _SENSOR_REQUIRED),
    "audio": (_AUDIO_KEYS, _AUDIO_REQUIRED),
    "radio": (_RADIO_KEYS, _RADIO_REQUIRED),
    "power": (_POWER_PERIPH_KEYS, _POWER_PERIPH_REQUIRED),
}

# --- Libraries section ---
_LIBRARY_KEYS = {"name": str, "for": str, "tested_version": str, "setup": list, "note": str}
_LIBRARY_REQUIRED = {"name", "for"}

# --- Validation section ---
_VALIDATION_KEYS = {"status": str, "note": str}
_VALIDATION_REQUIRED = {"status"}
VALIDATION_STATUSES = frozenset({"documented", "maintainer_verified"})

DATA_STATUS = (
    "From the manufacturer documents in `sources`, not physically validated. Board revisions "
    "and clones can differ: check the silkscreen before wiring."
)


class ProfileError(ValueError):
    """A profile file is malformed. Raised at load time, never shown as a tool result."""


def _check_keys(where: str, data: dict[str, Any], allowed: dict[str, type], required: set[str]) -> None:
    unknown = set(data) - set(allowed)
    if unknown:
        raise ProfileError(f"{where}: unknown keys {sorted(unknown)}")
    missing = required - set(data)
    if missing:
        raise ProfileError(f"{where}: missing keys {sorted(missing)}")
    for key, value in data.items():
        expected = allowed[key]
        # TOML writes 5.0 as a float but a careless 5 as an int; accept both for floats.
        if expected is float and isinstance(value, int) and not isinstance(value, bool):
            continue
        if not isinstance(value, expected) or isinstance(value, bool) and expected is not bool:
            raise ProfileError(f"{where}: {key} must be {expected.__name__}")


def _string_list(where: str, key: str, values: list[Any]) -> None:
    if not all(isinstance(value, str) and value for value in values):
        raise ProfileError(f"{where}: {key} must be a list of non-empty strings")


def _positive_int(where: str, key: str, value: Any) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ProfileError(f"{where}: {key} must be a positive integer")


def _validate_identity(where: str, identity: dict[str, Any]) -> None:
    """Validate the optional [identity] section."""
    _check_keys(f"{where} [identity]", identity, _IDENTITY_KEYS, _IDENTITY_REQUIRED)
    if "chip" in identity and identity["chip"] not in CHIP_NAMES:
        raise ProfileError(f"{where} [identity]: chip must be one of {sorted(CHIP_NAMES)}")
    if "usb" in identity:
        for i, entry in enumerate(identity["usb"]):
            if not isinstance(entry, dict):
                raise ProfileError(f"{where} [identity] usb[{i}]: must be a table")
            _check_keys(f"{where} [identity] usb[{i}]", entry, _USB_ENTRY_KEYS, {"vid", "pid"})
            if not _HEX4.fullmatch(entry["vid"]):
                raise ProfileError(f"{where} [identity] usb[{i}]: vid must be 4 lower-case hex digits")
            if not _HEX4.fullmatch(entry["pid"]):
                raise ProfileError(f"{where} [identity] usb[{i}]: pid must be 4 lower-case hex digits")
    for key in ("flash_mb", "psram_mb"):
        if key in identity:
            lst = identity[key]
            if not isinstance(lst, list):
                raise ProfileError(f"{where} [identity]: {key} must be a list")
            for v in lst:
                if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
                    raise ProfileError(f"{where} [identity]: {key} entries must be positive integers")


def _validate_build(where: str, build: dict[str, Any]) -> None:
    """Validate the optional [build] section."""
    _check_keys(f"{where} [build]", build, _BUILD_KEYS, _BUILD_REQUIRED)
    fqbn = build["fqbn"]
    parts = fqbn.split(":")
    if len(parts) != 3:
        raise ProfileError(f"{where} [build]: fqbn must have exactly three colon-separated parts")
    for part in parts:
        if not _FQBN_OPT.fullmatch(part):
            raise ProfileError(f"{where} [build]: fqbn part {part!r} has invalid characters")
    if "options" in build:
        for key, value in build["options"].items():
            if not _FQBN_OPT.fullmatch(key):
                raise ProfileError(f"{where} [build]: option key {key!r} has invalid characters")
            if not isinstance(value, str) or not _FQBN_OPT.fullmatch(value):
                raise ProfileError(f"{where} [build]: option value for {key!r} has invalid characters")


def _validate_peripheral(where: str, periph: dict[str, Any], gpio_set: set[int]) -> set[tuple[str, int]]:
    """Validate one [[peripherals]] entry. Returns the set of (role, gpio) claims."""
    if not isinstance(periph, dict):
        raise ProfileError(f"{where}: must be a table")
    kind = periph.get("kind")
    if not isinstance(kind, str) or kind not in PERIPHERAL_KINDS:
        raise ProfileError(f"{where}: kind must be one of {sorted(PERIPHERAL_KINDS)}")

    schema, required = _PERIPH_SCHEMA[kind]
    _check_keys(where, periph, schema, required)

    if "interface" in periph and periph["interface"] not in _INTERFACES_BY_KIND[kind]:
        raise ProfileError(f"{where}: interface must be one of {sorted(_INTERFACES_BY_KIND[kind])}")
    for key in ("name", "controller"):
        if key in periph and not periph[key].strip():
            raise ProfileError(f"{where}: {key} must be non-empty")

    # Kind-specific enum checks.
    if kind == "display":
        if periph["color"] not in DISPLAY_COLORS:
            raise ProfileError(f"{where}: color must be one of {sorted(DISPLAY_COLORS)}")
        _positive_int(where, "width", periph["width"])
        _positive_int(where, "height", periph["height"])
        if "backlight_active" in periph:
            if periph["backlight_active"] not in BACKLIGHT_ACTIVE:
                raise ProfileError(f"{where}: backlight_active must be one of {sorted(BACKLIGHT_ACTIVE)}")
        if "backlight" in periph.get("pins", {}) and "backlight_active" not in periph:
            raise ProfileError(f"{where}: backlight_active is required when pins has backlight")
    elif kind == "touch":
        if "width" in periph:
            _positive_int(where, "width", periph["width"])
        if "height" in periph:
            _positive_int(where, "height", periph["height"])
    elif kind == "button":
        if periph["active"] not in BUTTON_ACTIVE:
            raise ProfileError(f"{where}: active must be one of {sorted(BUTTON_ACTIVE)}")
        if periph["pull"] not in PULL_OPTIONS:
            raise ProfileError(f"{where}: pull must be one of {sorted(PULL_OPTIONS)}")

    # Validate pin roles and GPIO values.
    allowed_roles = _PIN_ROLES_BY_KIND[kind]
    claims: set[tuple[str, int]] = set()
    pins = periph.get("pins", {})
    if "pins" in periph:
        if not pins:
            raise ProfileError(f"{where}: pins must not be empty")
        if not isinstance(pins, dict):
            raise ProfileError(f"{where}: pins must be a table")
        for role, gpio in pins.items():
            if role not in allowed_roles:
                raise ProfileError(f"{where}: unknown pin role {role!r} for kind {kind}")
            if not isinstance(gpio, int) or isinstance(gpio, bool):
                raise ProfileError(f"{where}: pin {role} GPIO must be an integer")
            if gpio not in gpio_set:
                raise ProfileError(f"{where}: pin {role} GPIO {gpio} is not a [[pins]] entry of this profile")
            if gpio in {claimed_gpio for _, claimed_gpio in claims}:
                raise ProfileError(f"{where}: GPIO {gpio} has multiple roles in one peripheral")
            claims.add((role, gpio))
    return claims


def _validate_peripherals_cross(
    where: str, peripherals: list[dict[str, Any]], pin_data: dict[int, dict[str, Any]],
) -> None:
    """Cross-check all peripherals: GPIO in pins, reserved/caution annotation, no double-claim."""
    gpio_set = set(pin_data)
    all_claims: dict[int, list[tuple[str, str]]] = {}  # gpio -> [(kind, role), ...]
    for i, periph in enumerate(peripherals):
        claims = _validate_peripheral(f"{where} peripherals[{i}]", periph, gpio_set)
        kind = periph["kind"]
        for role, gpio in claims:
            all_claims.setdefault(gpio, []).append((kind, role))
            # Check the pin has reserved or caution text saying what it is wired to.
            pin = pin_data[gpio]
            if not (pin.get("reserved", "").strip() or pin.get("caution", "").strip()):
                raise ProfileError(
                    f"{where} peripherals[{i}]: GPIO {gpio} is claimed by {kind} "
                    f"but has no reserved or caution text in [[pins]]"
                )

    # Check for double-claims: one GPIO used by two peripherals is only allowed
    # when both declare the same shared bus role.
    for gpio, claims_list in all_claims.items():
        if len(claims_list) <= 1:
            continue
        roles = {role for _, role in claims_list}
        if len(roles) != 1 or not roles <= _SHARED_ROLES:
            names = [f"{kind}.{role}" for kind, role in claims_list]
            raise ProfileError(
                f"{where}: GPIO {gpio} is claimed by {', '.join(names)}; "
                f"only identical shared bus roles (mosi/sclk/miso/sda/scl) may overlap"
            )


def _validate_library(where: str, lib: dict[str, Any]) -> None:
    """Validate one [[libraries]] entry."""
    if not isinstance(lib, dict):
        raise ProfileError(f"{where}: must be a table")
    _check_keys(where, lib, _LIBRARY_KEYS, _LIBRARY_REQUIRED)
    if lib["for"] not in PERIPHERAL_KINDS:
        raise ProfileError(f"{where}: 'for' must be one of {sorted(PERIPHERAL_KINDS)}")
    if "setup" in lib:
        _string_list(where, "setup", lib["setup"])


def _validate_validation(where: str, val: dict[str, Any]) -> None:
    """Validate the optional [validation] section."""
    _check_keys(where, val, _VALIDATION_KEYS, _VALIDATION_REQUIRED)
    if val["status"] not in VALIDATION_STATUSES:
        raise ProfileError(f"{where}: status must be one of {sorted(VALIDATION_STATUSES)}")
    if val["status"] == "maintainer_verified" and not val.get("note", "").strip():
        raise ProfileError(f"{where}: maintainer_verified requires a note")


def validate(data: dict[str, Any], stem: str) -> dict[str, Any]:
    where = f"profiles/{stem}.toml"
    _check_keys(where, data, {**_REQUIRED, **_OPTIONAL}, set(_REQUIRED))
    if data["id"] != stem:
        raise ProfileError(f"{where}: id {data['id']!r} does not match the file name")
    if data["family"] not in FAMILIES:
        raise ProfileError(f"{where}: unknown family {data['family']!r}")
    if float(data["logic_voltage"]) not in LOGIC_VOLTAGES:
        raise ProfileError(f"{where}: logic_voltage must be one of {sorted(LOGIC_VOLTAGES)}")
    if data["five_volt_tolerant"] not in FIVE_VOLT_TOLERANCE:
        raise ProfileError(f"{where}: five_volt_tolerant must be one of {sorted(FIVE_VOLT_TOLERANCE)}")
    for key in ("fqbns", "flash_methods", "sources", "notes"):
        _string_list(where, key, data[key])

    has_build = "build" in data
    if data["family"] == "microcontroller" and not data["fqbns"] and not has_build:
        raise ProfileError(f"{where}: a microcontroller profile needs at least one FQBN or a [build] section")

    current = data["current_ma"]
    _check_keys(f"{where} [current_ma]", current, _CURRENT_KEYS, {"per_pin_recommended", "per_pin_absolute_max"})
    if not 0 < current["per_pin_recommended"] <= current["per_pin_absolute_max"]:
        raise ProfileError(f"{where}: per_pin_recommended must be positive and at most per_pin_absolute_max")

    for index, power in enumerate(data.get("power", [])):
        if not isinstance(power, dict):
            raise ProfileError(f"{where}: power[{index}] must be a table")
        _check_keys(f"{where} power[{index}]", power, _POWER_KEYS, set(_POWER_KEYS))

    seen_keys: set[Any] = set()
    seen_gpio: set[int] = set()
    pin_data: dict[int, dict[str, Any]] = {}
    for index, pin in enumerate(data["pins"]):
        pin_where = f"{where} pins[{index}]"
        if not isinstance(pin, dict):
            raise ProfileError(f"{pin_where} must be a table")
        _check_keys(pin_where, pin, _PIN_KEYS, {"name", "caps"})
        caps = pin["caps"]
        _string_list(pin_where, "caps", caps)
        if not caps or set(caps) - CAPS:
            raise ProfileError(f"{pin_where}: unknown caps {sorted(set(caps) - CAPS)}")
        if "reserved" in pin and "caution" in pin:
            raise ProfileError(f"{pin_where}: a pin is reserved or needs caution, not both")
        # Power and ground names repeat on a header; the physical position does not.
        key = ("physical", pin["physical"]) if "physical" in pin else ("name", pin["name"])
        if key in seen_keys:
            raise ProfileError(f"{pin_where}: duplicate pin {key[1]!r}")
        seen_keys.add(key)
        if "gpio" in pin:
            if pin["gpio"] in seen_gpio:
                raise ProfileError(f"{pin_where}: duplicate gpio {pin['gpio']}")
            seen_gpio.add(pin["gpio"])
            pin_data[pin["gpio"]] = pin

    # Validate new optional sections.
    if "identity" in data:
        _validate_identity(where, data["identity"])
    if has_build:
        _validate_build(where, data["build"])
    if "peripherals" in data:
        _validate_peripherals_cross(where, data["peripherals"], pin_data)
    if "libraries" in data:
        for i, lib in enumerate(data["libraries"]):
            _validate_library(f"{where} libraries[{i}]", lib)
    if "validation" in data:
        _validate_validation(f"{where} [validation]", data["validation"])

    return data


@functools.cache
def _load_all() -> dict[str, dict[str, Any]]:
    profiles: dict[str, dict[str, Any]] = {}
    owners: dict[str, str] = {}
    for path in sorted(PROFILE_DIR.glob("*.toml")):
        with path.open("rb") as handle:
            try:
                data = tomllib.load(handle)
            except tomllib.TOMLDecodeError as exc:
                raise ProfileError(f"profiles/{path.name}: {exc}") from exc
        profile = validate(data, path.stem)
        for fqbn in profile["fqbns"]:
            if fqbn in owners:
                raise ProfileError(f"FQBN {fqbn} is claimed by both {owners[fqbn]} and {profile['id']}")
            owners[fqbn] = profile["id"]
        profiles[profile["id"]] = profile
    return profiles


def load_profile(profile_id: str) -> dict[str, Any]:
    """Load only the requested profile, for the frequent stdlib-only panel scan."""
    if not isinstance(profile_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_\-]{0,79}", profile_id):
        raise not_found("profile id")
    path = PROFILE_DIR / f"{profile_id}.toml"
    try:
        with path.open("rb") as handle:
            return validate(tomllib.load(handle), profile_id)
    except FileNotFoundError as exc:
        raise not_found(f"profile {profile_id!r}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ProfileError(f"profiles/{path.name}: {exc}") from exc


def all_profiles() -> dict[str, dict[str, Any]]:
    return _load_all()


def _display_info(profile: dict[str, Any]) -> str | None:
    """Build a short display summary from the first display peripheral, if any."""
    for periph in profile.get("peripherals", []):
        if periph.get("kind") == "display":
            return f"{periph['controller']} {periph['width']}x{periph['height']}"
    return None


def index() -> list[dict[str, Any]]:
    result = []
    for p in _load_all().values():
        entry: dict[str, Any] = {
            "id": p["id"], "name": p["name"], "family": p["family"], "fqbns": list(p["fqbns"]),
        }
        identity = p.get("identity", {})
        entry["chip"] = identity.get("chip")
        entry["display"] = _display_info(p)
        result.append(entry)
    return result


def _split_fqbn(fqbn: str) -> tuple[str, dict[str, str]]:
    parts = fqbn.split(":")
    base = ":".join(parts[:3])
    options: dict[str, str] = {}
    if len(parts) > 3:
        for item in ":".join(parts[3:]).split(","):
            name, _, value = item.partition("=")
            options[name] = value
    return base, options


def profile_id_for_fqbn(fqbn: str | None) -> str | None:
    """Match an FQBN to a profile.

    A profile FQBN matches when the vendor:arch:board part is equal and its options
    are a subset of the given ones, so `arduino:avr:nano:cpu=atmega328old` still finds
    the Nano while a Nucleo profile keeps its `pnum`. The most specific match wins.
    """
    if not fqbn or fqbn.count(":") < 2:
        return None
    base, options = _split_fqbn(fqbn.strip())
    best: tuple[int, str] | None = None
    for profile in _load_all().values():
        for candidate in profile["fqbns"]:
            candidate_base, candidate_options = _split_fqbn(candidate)
            if candidate_base != base:
                continue
            if any(options.get(name) != value for name, value in candidate_options.items()):
                continue
            if best is None or len(candidate_options) > best[0]:
                best = (len(candidate_options), profile["id"])
    return best[1] if best else None


def _build_fqbn_full(build: dict[str, Any]) -> str:
    """Assemble fqbn_full: fqbn:key1=val1,key2=val2 with options sorted by key."""
    fqbn = build["fqbn"]
    options = build.get("options", {})
    if not options:
        return fqbn
    opt_str = ",".join(f"{k}={v}" for k, v in sorted(options.items()))
    return f"{fqbn}:{opt_str}"


def build_fqbn(profile_id: str) -> str | None:
    """The full FQBN for a profile with a [build] section, or None without one."""
    profile = _load_all().get(profile_id)
    if profile is None:
        raise ToolError(
            errors.PROFILE_NOT_FOUND,
            f"No board profile {profile_id!r}.",
            "Call board_profile with no arguments to list the profiles.",
        )
    build = profile.get("build")
    if build is None:
        return None
    return _build_fqbn_full(build)


def display_summary(profile_id: str) -> str | None:
    """Short display description, or None if no display peripheral."""
    profile = _load_all().get(profile_id)
    if profile is None:
        raise ToolError(
            errors.PROFILE_NOT_FOUND,
            f"No board profile {profile_id!r}.",
            "Call board_profile with no arguments to list the profiles.",
        )
    return _display_info(profile)


def _onboard_pins(profile: dict[str, Any]) -> dict[str, str]:
    """Map GPIO number (as string) to what it is wired to, from peripherals."""
    result: dict[str, str] = {}
    for periph in profile.get("peripherals", []):
        kind = periph.get("kind", "")
        pins = periph.get("pins", {})
        for role, gpio in pins.items():
            label = f"{kind} {role}"
            key = str(gpio)
            result[key] = f"{result[key]}; {label}" if key in result else label
    return result


def candidates(vid: str, pid: str, chip: str | None = None,
               flash_mb: int | None = None, psram_mb: int | None = None) -> list[str]:
    """Profile ids whose [identity] is compatible with the given USB/chip/memory params.

    Profiles without [identity] never match. Sorted, deterministic.
    """
    results: list[str] = []
    for profile in _load_all().values():
        identity = profile.get("identity")
        if identity is None:
            continue
        usb_list = identity.get("usb", [])
        if not any(e.get("vid") == vid and e.get("pid") == pid for e in usb_list):
            continue
        if chip is not None and "chip" in identity and identity["chip"] != chip:
            continue
        if flash_mb is not None and "flash_mb" in identity:
            if flash_mb not in identity["flash_mb"]:
                continue
        if psram_mb is not None and "psram_mb" in identity:
            if psram_mb not in (identity["psram_mb"] or [0]):
                continue
        results.append(profile["id"])
    results.sort()
    return results


def export(profile_id: str) -> dict[str, Any]:
    """A profile as returned to the model, with the pins to avoid pulled up front."""
    profile = _load_all().get(profile_id)
    if profile is None:
        raise ToolError(
            errors.PROFILE_NOT_FOUND,
            f"No board profile {profile_id!r}.",
            "Call board_profile with no arguments to list the profiles.",
        )
    pins = profile["pins"]
    result: dict[str, Any] = {**profile}
    result["reserved_pins"] = [{"name": p["name"], "reason": p["reserved"]} for p in pins if "reserved" in p]
    result["caution_pins"] = [{"name": p["name"], "reason": p["caution"]} for p in pins if "caution" in p]

    # Build section enrichment.
    build = profile.get("build")
    if build is not None:
        result["build"] = {**build, "fqbn_full": _build_fqbn_full(build)}

    # Onboard pins map.
    onboard = _onboard_pins(profile)
    if "peripherals" in profile:
        result["onboard_pins"] = onboard

    # Data status: respect validation.
    validation = profile.get("validation")
    if validation and validation.get("status") == "maintainer_verified":
        result["data_status"] = f"Maintainer verified. {validation.get('note', '')}"
    else:
        result["data_status"] = DATA_STATUS

    return result


def not_found(what: str) -> ToolError:
    return ToolError(
        errors.PROFILE_NOT_FOUND,
        f"No board profile matches {what}.",
        "Pass fqbn= for a board that USB cannot identify (for example arduino:avr:nano for a CH340 Nano "
        "clone), or call board_profile with no arguments to list the profiles.",
    )
