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
_OPTIONAL = {"led_builtin": str, "power": list}
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
        if not isinstance(value, expected) or isinstance(value, bool):
            raise ProfileError(f"{where}: {key} must be {expected.__name__}")


def _string_list(where: str, key: str, values: list[Any]) -> None:
    if not all(isinstance(value, str) and value for value in values):
        raise ProfileError(f"{where}: {key} must be a list of non-empty strings")


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
    if data["family"] == "microcontroller" and not data["fqbns"]:
        raise ProfileError(f"{where}: a microcontroller profile needs at least one FQBN")

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


def all_profiles() -> dict[str, dict[str, Any]]:
    return _load_all()


def index() -> list[dict[str, Any]]:
    return [
        {"id": p["id"], "name": p["name"], "family": p["family"], "fqbns": list(p["fqbns"])}
        for p in _load_all().values()
    ]


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
    return {
        **profile,
        "reserved_pins": [{"name": p["name"], "reason": p["reserved"]} for p in pins if "reserved" in p],
        "caution_pins": [{"name": p["name"], "reason": p["caution"]} for p in pins if "caution" in p],
        "data_status": DATA_STATUS,
    }


def not_found(what: str) -> ToolError:
    return ToolError(
        errors.PROFILE_NOT_FOUND,
        f"No board profile matches {what}.",
        "Pass fqbn= for a board that USB cannot identify (for example arduino:avr:nano for a CH340 Nano "
        "clone), or call board_profile with no arguments to list the profiles.",
    )
