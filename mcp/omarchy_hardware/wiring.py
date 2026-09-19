"""Check a wiring plan against a board profile, deterministically.

The model proposes the wiring; this code applies the electrical rules, in the same
way policy.py enforces allowlists: the verdict does not depend on the model being
careful. Every finding names the pin and says what to change.

A connection is one wire (or bus line) from a board pin to a part:

    {"pin": "D3", "part": "SG90 servo", "role": "pwm", "load": "servo"}
    {"pin": "A4", "part": "BME280", "role": "i2c_sda", "part_voltage": 3.3,
     "i2c_address": "0x76", "pull_up": true}

It checks the electrical plan, not the code or the physical wiring. A clean result
is necessary, not sufficient.
"""

from __future__ import annotations

import re
from typing import Any

from . import errors
from .errors import ToolError

ROLES = {
    # role: (capability the pin must have, whether a missing capability is only a warning)
    "digital_out": ("digital", False),
    "digital_in": (None, False),
    "pwm": ("pwm", False),
    "analog_in": ("analog_in", False),
    "dac": ("dac", False),
    "interrupt": ("interrupt", False),
    "onewire": ("digital", False),
    "i2c_sda": ("i2c_sda", True),
    "i2c_scl": ("i2c_scl", True),
    "spi_mosi": ("spi_mosi", True),
    "spi_miso": ("spi_miso", True),
    "spi_sck": ("spi_sck", True),
    "spi_cs": (None, False),
    "uart_tx": ("uart_tx", True),
    "uart_rx": ("uart_rx", True),
    "power": (None, False),
    "ground": (None, False),
}
# Roles where the board drives the line, so a lower-voltage part is at risk.
BOARD_DRIVES = {"digital_out", "pwm", "dac", "spi_mosi", "spi_sck", "spi_cs", "uart_tx", "onewire"}
I2C = {"i2c_sda", "i2c_scl"}
# Roles several parts may share on one pin.
SHARED = {"i2c_sda", "i2c_scl", "spi_mosi", "spi_miso", "spi_sck", "power", "ground", "onewire"}
LOADS = {"none", "led", "relay", "motor", "servo", "solenoid", "buzzer", "other_inductive"}
INDUCTIVE = {"relay", "motor", "solenoid", "other_inductive"}
MAX_CONNECTIONS = 80
_KEYS = {"pin", "part", "role", "part_voltage", "load", "current_ma", "i2c_address", "driver", "pull_up"}
_ADDRESS = re.compile(r"0x[0-7][0-9a-f]")


def _bad(message: str) -> ToolError:
    return ToolError(errors.INVALID_ARGUMENT, message, "See the wiring_check description for the connection format.")


def _number(value: object, label: str, low: float, high: float) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float) or not low <= value <= high:
        raise _bad(f"{label} must be a number from {low} to {high}.")
    return float(value)


def _parse(connections: object) -> list[dict[str, Any]]:
    if not isinstance(connections, list) or not connections or len(connections) > MAX_CONNECTIONS:
        raise _bad(f"connections must be a list of 1 to {MAX_CONNECTIONS} entries.")
    parsed = []
    for index, raw in enumerate(connections):
        where = f"connections[{index}]"
        if not isinstance(raw, dict) or set(raw) - _KEYS:
            raise _bad(f"{where} must be an object with keys from {sorted(_KEYS)}.")
        pin, part, role = raw.get("pin"), raw.get("part", ""), raw.get("role")
        if not isinstance(pin, str) or not 0 < len(pin) <= 16:
            raise _bad(f"{where}.pin must be a pin name such as D3, GPIO21 or GP4.")
        if not isinstance(part, str) or len(part) > 60:
            raise _bad(f"{where}.part must be a name of at most 60 characters.")
        if role not in ROLES:
            raise _bad(f"{where}.role must be one of {', '.join(ROLES)}.")
        load = raw.get("load", "none")
        if load not in LOADS:
            raise _bad(f"{where}.load must be one of {', '.join(sorted(LOADS))}.")
        address = raw.get("i2c_address")
        if address is not None and (not isinstance(address, str) or not _ADDRESS.fullmatch(address.lower())):
            raise _bad(f"{where}.i2c_address must be a 7-bit address like 0x76.")
        for flag in ("driver", "pull_up"):
            if raw.get(flag) is not None and not isinstance(raw[flag], bool):
                raise _bad(f"{where}.{flag} must be true or false.")
        parsed.append(
            {
                "pin": pin.strip(),
                "part": " ".join(part.split()) or "part",
                "role": role,
                "part_voltage": _number(raw.get("part_voltage"), f"{where}.part_voltage", 0, 30),
                "load": load,
                "current_ma": _number(raw.get("current_ma"), f"{where}.current_ma", 0, 5000),
                "i2c_address": address.lower() if address else None,
                "driver": raw.get("driver"),
                "pull_up": raw.get("pull_up"),
            }
        )
    return parsed


def _pin_lookup(profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for pin in profile["pins"]:
        lookup.setdefault(pin["name"].upper(), pin)
        if "physical" in pin:
            lookup.setdefault(f"PIN{pin['physical']}", pin)
        if "mcu_pin" in pin:
            lookup.setdefault(pin["mcu_pin"].upper(), pin)
    for power in profile.get("power", []):
        lookup.setdefault(power["name"].upper(), {"name": power["name"], "caps": ["power"], "note": power["note"]})
    for alias in ("GND", "GROUND"):
        lookup.setdefault(alias, {"name": "GND", "caps": ["ground"]})
    return lookup


def check(profile: dict[str, Any], connections: object) -> dict[str, Any]:
    plan = _parse(connections)
    lookup = _pin_lookup(profile)
    logic = float(profile["logic_voltage"])
    tolerant = profile["five_volt_tolerant"]
    limits = profile["current_ma"]
    findings: list[dict[str, str]] = []

    def add(severity: str, pin: str, message: str) -> None:
        findings.append({"severity": severity, "pin": pin, "message": message})

    by_pin: dict[str, list[dict[str, Any]]] = {}
    total_ma = 0.0
    for wire in plan:
        pin = lookup.get(wire["pin"].upper())
        label = wire["pin"]
        if pin is None:
            add("error", label, f"{label} is not a pin on {profile['name']}. Use a name from board_profile.")
            continue
        label = pin["name"]
        by_pin.setdefault(label, []).append(wire)
        caps = set(pin["caps"])
        role = wire["role"]

        if "reserved" in pin:
            add("error", label, f"{label} is reserved: {pin['reserved']}")
        elif "caution" in pin:
            add("warning", label, f"{label} needs care: {pin['caution']}")

        if role in ("power", "ground"):
            if not caps & {"power", "ground"}:
                add("error", label, f"{label} is a signal pin, not a supply. Power {wire['part']} from a supply pin.")
            continue
        if caps & {"power", "ground"}:
            add("error", label, f"{label} is a supply pin; it cannot carry the {role} signal for {wire['part']}.")
            continue

        needed, soft = ROLES[role]
        if role in BOARD_DRIVES and "input_only" in caps:
            add("error", label, f"{label} is input-only; it cannot drive {wire['part']}.")
        elif needed and needed not in caps and not (needed == "digital" and "input_only" in caps):
            if soft:
                add("warning", label, f"{label} is not the default {role} pin. It only works if the core lets you "
                                      f"move that bus; the default pin is in board_profile.")
            else:
                add("error", label, f"{label} cannot do {role} (capabilities: {', '.join(sorted(caps))}).")

        volts = wire["part_voltage"]
        if volts is not None:
            if volts > logic + 0.3:
                if tolerant == "all":
                    pass
                elif tolerant == "some":
                    add("warning", label, f"{wire['part']} uses {volts:g} V logic on a {logic:g} V board. Only some "
                                          f"pins are 5 V tolerant; check {label} in the datasheet or add a level "
                                          f"shifter.")
                else:
                    add("error", label, f"{wire['part']} uses {volts:g} V logic but {profile['name']} is {logic:g} V "
                                        f"and not 5 V tolerant. Add a level shifter or a divider.")
            elif volts < logic - 0.3 and role in I2C:
                add("warning", label, f"I2C is open-drain, so the pull-ups set the high level. If they go to "
                                      f"{logic:g} V, the {volts:g} V {wire['part']} needs a level shifter; many "
                                      f"breakouts have one on board.")
            elif volts < logic - 0.3 and role in BOARD_DRIVES:
                add("error", label, f"{label} drives {logic:g} V into {wire['part']}, a {volts:g} V part. Add a level "
                                    f"shifter, or use a board with {volts:g} V logic.")
            elif volts < logic - 0.3:
                add("info", label, f"{wire['part']} answers at {volts:g} V on a {logic:g} V board: usually read as "
                                   f"high, but check the input threshold.")

        if wire["load"] in INDUCTIVE:
            if not wire["driver"]:
                add("error", label, f"Never drive a {wire['load']} from a pin. Use a transistor, MOSFET or driver "
                                    f"module, with a flyback diode, on its own supply.")
            else:
                add("info", label, f"The {wire['load']} driver needs a flyback diode (modules usually have one) and a "
                                   f"supply of its own with a common ground.")
        elif wire["load"] == "servo":
            add("info", label, "Power the servo from its own 5 V supply, not the board, and join the grounds.")

        current = wire["current_ma"]
        if current is not None and not (wire["driver"] and wire["load"] in INDUCTIVE):
            total_ma += current
            if current > limits["per_pin_absolute_max"]:
                add("error", label, f"{current:g} mA exceeds the {limits['per_pin_absolute_max']} mA absolute maximum "
                                    f"of {label}. Switch it through a transistor.")
            elif current > limits["per_pin_recommended"]:
                add("warning", label, f"{current:g} mA is above the recommended {limits['per_pin_recommended']} mA "
                                      f"for {label}. Raise the series resistor or use a transistor.")

    if "total_max" in limits and total_ma > limits["total_max"]:
        add("error", "*", f"The pins source {total_ma:g} mA in total, over the {limits['total_max']} mA limit.")

    for pin_name, wires in by_pin.items():
        roles = {wire["role"] for wire in wires}
        if len(wires) > 1 and not roles <= SHARED:
            add("error", pin_name, f"{pin_name} is used for {', '.join(sorted(roles))}. Give each signal its own pin.")
        elif len(roles) > 1:
            add("error", pin_name, f"{pin_name} is wired as {', '.join(sorted(roles))} at once.")

    i2c = [wire for wire in plan if wire["role"] in ("i2c_sda", "i2c_scl")]
    if i2c:
        lines = {wire["role"] for wire in i2c}
        if lines != {"i2c_sda", "i2c_scl"}:
            missing = ({"i2c_sda", "i2c_scl"} - lines).pop()
            add("error", "*", f"The I2C bus has no {missing.split('_')[1].upper()} line.")
        if not any(wire["pull_up"] for wire in i2c):
            add("warning", "*", "Nothing says the I2C bus has pull-ups. Most breakout boards include them; "
                                "otherwise add 4.7 kOhm from SDA and SCL to the logic supply.")
        owners: dict[str, set[str]] = {}
        for wire in i2c:
            if wire["i2c_address"]:
                owners.setdefault(wire["i2c_address"], set()).add(wire["part"])
        for address, names in owners.items():
            if len(names) > 1:
                add("error", "*", f"{' and '.join(sorted(names))} share I2C address {address}. Change one with its "
                                  f"address jumper or use a multiplexer.")

    order = {"error": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda finding: order[finding["severity"]])
    counts = {severity: sum(1 for f in findings if f["severity"] == severity) for severity in order}
    verdict = "fail" if counts["error"] else "check" if counts["warning"] else "pass"
    return {"board": profile["id"], "verdict": verdict, "counts": counts, "findings": findings,
            "note": "Checks the electrical plan only, from datasheet data. A pass is necessary, not sufficient."}
