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
MIN_SSH_TIMEOUT = 1
MAX_SSH_TIMEOUT = 60
MIN_WRITE_BYTES = 1
MAX_WRITE_BYTES = 4096
MIN_WRITE_BUDGET = 1
MAX_WRITE_BUDGET = 65536


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    pi_hosts: tuple[str, ...] = ()
    pi_host_keys: dict[str, str] = field(default_factory=dict)  # host -> ssh-fingerprint
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
    if (
        not isinstance(pins, list)
        or not all(isinstance(pin, int) and not isinstance(pin, bool) and 2 <= pin <= 27 for pin in pins)
        or len(set(pins)) != len(pins)
    ):
        raise ConfigError("pi.allowed_pins must contain unique integers in 2-27")

    hosts = pi.get("hosts", [])
    if (
        not isinstance(hosts, list)
        or not all(
            isinstance(host, str)
            and host
            and not any(char.isspace() or ord(char) < 32 for char in host)
            for host in hosts
        )
        or len(set(hosts)) != len(hosts)
    ):
        raise ConfigError("pi.hosts must contain unique non-empty strings without whitespace or control characters")

    ssh_timeout = pi.get("ssh_timeout", 10)
    max_write_bytes = serial.get("max_write_bytes", 4096)
    write_budget_bytes_per_min = serial.get("write_budget_bytes_per_min", 65536)
    allow_flash = flash.get("allow", True)
    if (
        not isinstance(ssh_timeout, int)
        or isinstance(ssh_timeout, bool)
        or not MIN_SSH_TIMEOUT <= ssh_timeout <= MAX_SSH_TIMEOUT
    ):
        raise ConfigError(f"pi.ssh_timeout must be an integer in {MIN_SSH_TIMEOUT}-{MAX_SSH_TIMEOUT}")
    if (
        not isinstance(max_write_bytes, int)
        or isinstance(max_write_bytes, bool)
        or not MIN_WRITE_BYTES <= max_write_bytes <= MAX_WRITE_BYTES
    ):
        raise ConfigError(f"serial.max_write_bytes must be an integer in {MIN_WRITE_BYTES}-{MAX_WRITE_BYTES}")
    if (
        not isinstance(write_budget_bytes_per_min, int)
        or isinstance(write_budget_bytes_per_min, bool)
        or not MIN_WRITE_BUDGET <= write_budget_bytes_per_min <= MAX_WRITE_BUDGET
    ):
        raise ConfigError(
            f"serial.write_budget_bytes_per_min must be an integer in {MIN_WRITE_BUDGET}-{MAX_WRITE_BUDGET}"
        )
    if not isinstance(allow_flash, bool):
        raise ConfigError("flash.allow must be a boolean")

    return Config(
        pi_hosts=tuple(hosts),
        pi_allowed_pins=tuple(pins),
        pi_ssh_timeout=ssh_timeout,
        max_write_bytes=max_write_bytes,
        write_budget_bytes_per_min=write_budget_bytes_per_min,
        allow_flash=allow_flash,
        extra=raw,
    )
