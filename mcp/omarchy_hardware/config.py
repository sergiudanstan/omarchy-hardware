"""User configuration, read from ~/.config/omarchy-hardware/config.toml."""

from __future__ import annotations

import os
import stat
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "omarchy-hardware"
CONFIG_PATH = CONFIG_DIR / "config.toml"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "omarchy-hardware"

# BCM 0 and 1 are the HAT ID EEPROM pins; driving them can confuse board detection.
DEFAULT_PINS = tuple(n for n in range(2, 28))


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    pi_hosts: tuple[str, ...] = ()
    pi_allowed_pins: tuple[int, ...] = DEFAULT_PINS
    pi_ssh_timeout: int = 10
    max_write_bytes: int = 4096
    write_budget_bytes_per_min: int = 65536
    allow_flash: bool = True
    extra: dict = field(default_factory=dict)


def _check_permissions(path: Path) -> None:
    mode = path.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ConfigError(
            f"{path} is group- or world-accessible; it names the hosts this plugin may "
            f"reach over SSH. Run: chmod 600 {path}"
        )


def load() -> Config:
    if not CONFIG_PATH.exists():
        return Config()

    _check_permissions(CONFIG_PATH)

    with CONFIG_PATH.open("rb") as handle:
        raw = tomllib.load(handle)

    pi = raw.get("pi", {})
    serial = raw.get("serial", {})
    flash = raw.get("flash", {})

    pins = pi.get("allowed_pins", list(DEFAULT_PINS))
    if not all(isinstance(pin, int) and 0 <= pin <= 27 for pin in pins):
        raise ConfigError("pi.allowed_pins must be integers in 0-27")

    hosts = pi.get("hosts", [])
    if not all(isinstance(host, str) and host for host in hosts):
        raise ConfigError("pi.hosts must be non-empty strings")

    return Config(
        pi_hosts=tuple(hosts),
        pi_allowed_pins=tuple(pins),
        pi_ssh_timeout=int(pi.get("ssh_timeout", 10)),
        max_write_bytes=int(serial.get("max_write_bytes", 4096)),
        write_budget_bytes_per_min=int(serial.get("write_budget_bytes_per_min", 65536)),
        allow_flash=bool(flash.get("allow", True)),
        extra=raw,
    )
