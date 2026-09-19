import subprocess

import pytest

from omarchy_hardware import discovery, server
from omarchy_hardware.config import Config

AVAHI = (
    "+;wlp2s0;IPv4;raspberrypi;_ssh._tcp;local\n"
    "=;wlp2s0;IPv4;raspberrypi;_ssh._tcp;local;raspberrypi.local;192.168.68.40;22;\n"
    "=;wlp2s0;IPv4;bench\\032server;_ssh._tcp;local;bench.local;192.168.68.12;2222;\n"
    "=;wlp2s0;IPv6;raspberrypi;_ssh._tcp;local;raspberrypi.local;fe80::1%wlp2s0;22;\n"
    "=;wlp2s0;IPv4;evil;_ssh._tcp;local;-oProxyCommand=id;192.168.68.66;22;\n"
    "=;wlp2s0;IPv4;junk;_ssh._tcp;local;junk.local;not-an-ip;22;\n"
)
ARP = (
    "IP address       HW type     Flags       HW address            Mask     Device\n"
    "192.168.68.40    0x1         0x2         d8:3a:dd:11:22:33     *        wlp2s0\n"
    "192.168.68.41    0x1         0x2         2c:cf:67:aa:bb:cc     *        wlp2s0\n"
    "192.168.68.42    0x1         0x0         b8:27:eb:00:00:00     *        wlp2s0\n"
    "192.168.68.1     0x1         0x2         3c:6a:d2:94:d5:dc     *        wlp2s0\n"
)


def test_parse_avahi_keeps_resolved_valid_entries():
    hosts = discovery.parse_avahi(AVAHI)
    assert [(h["hostname"], h["address"], h["ssh_port"]) for h in hosts] == [
        ("raspberrypi.local", "192.168.68.40", 22),
        ("bench.local", "192.168.68.12", 2222),
        ("raspberrypi.local", "fe80::1", 22),
    ]
    assert hosts[1]["service_name"] == "bench server"


def test_parse_arp_keeps_only_complete_raspberry_pi_entries():
    hosts = discovery.parse_arp(ARP)
    assert [h["address"] for h in hosts] == ["192.168.68.40", "192.168.68.41"]
    assert all("mac" not in h for h in hosts), "full MAC addresses are not returned"


@pytest.fixture
def network(monkeypatch, tmp_path):
    arp = tmp_path / "arp"
    arp.write_text(ARP)
    monkeypatch.setattr(discovery, "ARP_TABLE", arp)
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[0] == discovery.AVAHI_BROWSE:
            return subprocess.CompletedProcess(argv, 0, AVAHI, "")
        known = argv[-1] == "raspberrypi.local"
        return subprocess.CompletedProcess(argv, 0 if known else 1, "# Host found\nkey" if known else "", "")

    monkeypatch.setattr(discovery.subprocess, "run", fake_run)
    return calls


def test_discover_merges_sources_and_reports_status(network):
    result = discovery.discover(Config(pi_hosts=("raspberrypi.local",)))
    by_address = {h["address"]: h for h in result["hosts"]}
    pi = by_address["192.168.68.40"]
    assert pi["likely_raspberry_pi"] and pi["mac_vendor"] == "Raspberry Pi"
    assert pi["in_pi_hosts"] and pi["host_key_known"]
    arp_only = by_address["192.168.68.41"]
    assert arp_only["likely_raspberry_pi"] and not arp_only["in_pi_hosts"] and not arp_only["host_key_known"]
    assert not by_address["192.168.68.12"]["likely_raspberry_pi"]
    assert result["hosts"][0]["likely_raspberry_pi"], "likely Pis are listed first"
    assert "only the user adds" in result["how_to_add"].lower()


def test_discover_never_passes_an_option_like_host_to_ssh_keygen(network):
    discovery.discover(Config())
    keygen = [argv for argv in network if argv[0] == discovery.SSH_KEYGEN]
    assert keygen and all(argv[1] == "-F" and not argv[2].startswith("-") for argv in keygen)
    assert discovery.AVAHI_BROWSE in network[0][0]


def test_discover_without_avahi(monkeypatch, tmp_path):
    arp = tmp_path / "arp"
    arp.write_text(ARP)
    monkeypatch.setattr(discovery, "ARP_TABLE", arp)

    def missing(argv, **kwargs):
        if argv[0] == discovery.AVAHI_BROWSE:
            raise FileNotFoundError(argv[0])
        return subprocess.CompletedProcess(argv, 1, "", "")

    monkeypatch.setattr(discovery.subprocess, "run", missing)
    monkeypatch.setattr(server, "_config", lambda: Config())
    result = server.pi_discover()
    assert result["ok"] is True and "avahi-browse" in result["warning"]
    assert len(result["hosts"]) == 2
