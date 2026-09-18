import pytest
from modbus_fake import FakeModbusServer

from omarchy_hardware import server
from omarchy_hardware.config import (
    Config,
    ConfigError,
    ModbusRange,
    WeintekModbusTarget,
    _parse_weintek_modbus,
)
from omarchy_hardware.errors import HOST_NOT_ALLOWED, INVALID_ARGUMENT, SERVICE_ERROR
from omarchy_hardware.server import weintek_modbus_read


@pytest.fixture
def hmi(monkeypatch):
    fake = FakeModbusServer(holding={100: 215, 101: 40, 9999: 7}, coils={5: 1, 7: 1})
    target = WeintekModbusTarget(
        "127.0.0.1",
        fake.port,
        1,
        (ModbusRange("LW", 100, 8), ModbusRange("RW", 0, 4), ModbusRange("LB", 0, 16)),
    )
    monkeypatch.setattr(server, "_config", lambda: Config(weintek_allow=True, weintek_modbus=(target,)))
    yield fake
    fake.close()


def test_reads_words_and_bits_with_easybuilder_address_mapping(hmi):
    words = weintek_modbus_read("127.0.0.1", "LW-100", count=2, port=hmi.port)
    assert words["ok"] is True, words
    assert words["values"] == [215, 40]

    rw = weintek_modbus_read("127.0.0.1", "RW-0", port=hmi.port)
    assert rw["values"] == [7]

    bits = weintek_modbus_read("127.0.0.1", "LB-4", count=4, port=hmi.port)
    assert bits["values"] == [0, 1, 0, 1]

    # LW-n is holding register n, RW-0 is 4x 10000 (protocol 9999), LB-n is coil n.
    assert hmi.requests == [(0x03, 100, 2), (0x03, 9999, 1), (0x01, 4, 4)]


def test_a_range_must_sit_inside_one_allowlisted_range(hmi):
    for address, count in (("LW-99", 1), ("LW-107", 2), ("RW-4", 1), ("LB-16", 1)):
        refused = weintek_modbus_read("127.0.0.1", address, count=count, port=hmi.port)
        assert refused["error"]["code"] == HOST_NOT_ALLOWED, address
    assert hmi.requests == []


def test_bad_addresses_and_counts_are_rejected(hmi):
    for address in ("LW100", "DW-1", "LW--1", "LW-1;LW-2"):
        assert weintek_modbus_read("127.0.0.1", address, port=hmi.port)["error"]["code"] == INVALID_ARGUMENT
    assert weintek_modbus_read("127.0.0.1", "LW-100", count=65, port=hmi.port)["error"]["code"] == INVALID_ARGUMENT


def test_modbus_exceptions_are_reported(monkeypatch):
    fake = FakeModbusServer(exception=2)
    target = WeintekModbusTarget("127.0.0.1", fake.port, 1, (ModbusRange("LW", 0, 4),))
    monkeypatch.setattr(server, "_config", lambda: Config(weintek_allow=True, weintek_modbus=(target,)))
    try:
        failed = weintek_modbus_read("127.0.0.1", "LW-0", port=fake.port)
    finally:
        fake.close()
    assert failed["error"]["code"] == SERVICE_ERROR
    assert "illegal data address" in failed["error"]["message"]


def test_config_requires_the_insecure_waiver_and_valid_ranges():
    ok = _parse_weintek_modbus({"weintek": {"modbus": [
        {"host": "hmi.local", "allow_insecure": True, "read": ["LW-0:16", "LB-5", "RW-100:4"]}
    ]}})
    assert ok[0].port == 502 and ok[0].unit == 1
    assert ok[0].read == (ModbusRange("LW", 0, 16), ModbusRange("LB", 5, 1), ModbusRange("RW", 100, 4))

    for entry, message in (
        ({"host": "hmi.local", "read": ["LW-0"]}, "allow_insecure"),
        ({"host": "hmi.local", "allow_insecure": True, "read": []}, "non-empty"),
        ({"host": "hmi.local", "allow_insecure": True, "read": ["LW-9998:5"]}, "past the end"),
        ({"host": "hmi.local", "allow_insecure": True, "read": ["D-0"]}, "must look like"),
        ({"host": "-oProxy", "allow_insecure": True, "read": ["LW-0"]}, "hostname"),
    ):
        with pytest.raises(ConfigError, match=message):
            _parse_weintek_modbus({"weintek": {"modbus": [entry]}})
