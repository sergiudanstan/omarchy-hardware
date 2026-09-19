"""Find Raspberry Pis on the local network, read-only.

Two sources, neither of which sends anything to the Pi itself:

- mDNS: `avahi-browse -rtpk _ssh._tcp` lists hosts that advertise SSH (Raspberry
  Pi OS does by default), with their .local name and address.
- The kernel's ARP cache (/proc/net/arp): addresses whose MAC belongs to
  Raspberry Pi Ltd are Pis even without mDNS.

Nothing here adds a host or trusts a key. A Pi becomes usable only when the user
checks its SSH host key and adds it to [pi] hosts themselves; the result says how.
MAC addresses are reduced to the vendor, not returned.
"""

from __future__ import annotations

import ipaddress
import re
import subprocess
from pathlib import Path
from typing import Any

from .config import Config, _valid_host

AVAHI_BROWSE = "/usr/bin/avahi-browse"
SSH_KEYGEN = "/usr/bin/ssh-keygen"
ARP_TABLE = Path("/proc/net/arp")
BROWSE_TIMEOUT = 6
MAX_HOSTS = 64

# MAC prefixes (OUIs) registered to the Raspberry Pi Foundation and Raspberry Pi Ltd.
RPI_OUIS = frozenset({"b8:27:eb", "dc:a6:32", "e4:5f:01", "28:cd:c1", "d8:3a:dd", "2c:cf:67"})

_ESCAPE = re.compile(r"\\(\d{3})")


def _unescape(text: str) -> str:
    """avahi -p escapes separators and odd bytes as \\DDD (decimal)."""
    return _ESCAPE.sub(lambda m: chr(int(m.group(1))) if int(m.group(1)) < 128 else "?", text)


def _address(text: str) -> str | None:
    try:
        return str(ipaddress.ip_address(text.split("%", 1)[0]))
    except ValueError:
        return None


def parse_avahi(output: str) -> list[dict[str, Any]]:
    hosts = []
    for line in output.splitlines():
        # =;iface;proto;name;type;domain;hostname;address;port;txt
        fields = line.split(";")
        if len(fields) < 9 or fields[0] != "=":
            continue
        address = _address(fields[7])
        hostname = _unescape(fields[6]).rstrip(".")
        if address is None or not _valid_host(hostname):
            continue
        port = int(fields[8]) if fields[8].isdigit() and 0 < int(fields[8]) < 65536 else 22
        hosts.append({
            "address": address,
            "hostname": hostname,
            "service_name": "".join(ch for ch in _unescape(fields[3]) if ch.isprintable())[:60],
            "ssh_port": port,
            "interface": fields[1][:16],
        })
    return hosts


def parse_arp(text: str) -> list[dict[str, Any]]:
    hosts = []
    for line in text.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 6:
            continue
        address, flags, mac = _address(fields[0]), fields[2], fields[3].lower()
        # Flag 0x2 is a completed entry; 00:00:00:00:00:00 is an unresolved one.
        if address is None or flags == "0x0" or mac[:8] not in RPI_OUIS:
            continue
        hosts.append({"address": address, "mac_vendor": "Raspberry Pi", "interface": fields[5][:16]})
    return hosts


def _browse() -> tuple[str, str | None]:
    try:
        # S603: fixed argv, no input from the model.
        result = subprocess.run(  # noqa: S603
            [AVAHI_BROWSE, "-r", "-t", "-p", "-k", "_ssh._tcp"],
            capture_output=True, text=True, timeout=BROWSE_TIMEOUT, check=False,
        )
    except FileNotFoundError:
        return "", "avahi-browse is not installed, so mDNS names were not looked up."
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return output, None
    return result.stdout, None


def _known_host(host: str) -> bool:
    if not _valid_host(host):
        return False
    try:
        # S603: fixed argv; host passed _valid_host (no leading dash, no whitespace).
        result = subprocess.run(  # noqa: S603
            [SSH_KEYGEN, "-F", host], capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def discover(config: Config) -> dict[str, Any]:
    output, warning = _browse()
    try:
        arp = parse_arp(ARP_TABLE.read_text())
    except OSError:
        arp = []

    merged: dict[str, dict[str, Any]] = {}
    for host in parse_avahi(output):
        merged.setdefault(host["address"], {}).update(host)
    for host in arp:
        entry = merged.setdefault(host["address"], {"address": host["address"]})
        entry.setdefault("interface", host["interface"])
        entry["mac_vendor"] = host["mac_vendor"]

    pi_targets = set(config.pi_hosts)
    jetson_targets = set(config.jetson_hosts)
    hosts = []
    for entry in list(merged.values())[:MAX_HOSTS]:
        names = [name for name in (entry.get("hostname"), entry["address"]) if name]
        likely_pi = entry.get("mac_vendor") == "Raspberry Pi" or any(
            "raspberrypi" in name.lower() for name in names
        )
        hosts.append({
            **entry,
            "likely_raspberry_pi": likely_pi,
            "in_pi_hosts": any(name in pi_targets for name in names),
            "in_jetson_hosts": any(name in jetson_targets for name in names),
            "host_key_known": any(_known_host(name) for name in names),
        })
    hosts.sort(key=lambda host: (not host["likely_raspberry_pi"], host.get("hostname") or host["address"]))
    result: dict[str, Any] = {
        "hosts": hosts,
        "how_to_add": (
            "Only the user adds a Pi. On the Pi, run `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`. Then "
            "connect once with `ssh <user>@<host>` from a terminal and accept the key only if the fingerprints "
            "match. Then add the host to [pi] hosts in ~/.config/omarchy-hardware/config.toml."
        ),
    }
    if warning:
        result["warning"] = warning
    return result
