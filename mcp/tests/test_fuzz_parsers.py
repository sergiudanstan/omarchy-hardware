"""Randomised input against every parser that reads device or network bytes.

Each parser must return normally or raise its own module's error, never anything
else: a stray IndexError or UnicodeDecodeError here becomes "the tool failed
unexpectedly" for the model, or ends a background thread. Seeded, so a failure
reproduces; no dependency beyond the standard library.
"""

# ruff: noqa: S311 - seeded, reproducible test input, not anything secret

from __future__ import annotations

import json
import random
import socket
import struct
import threading
import time

import pytest

from omarchy_hardware import bridge, crash, fingerprint, http_lite, micropython, ming, mqtt_lite, weintek
from omarchy_hardware.config import ModbusRange, WeintekModbusTarget
from omarchy_hardware.errors import ToolError

ROUNDS = 300


def _bytes(rng: random.Random, size: int = 64) -> bytes:
    return bytes(rng.getrandbits(8) for _ in range(rng.randrange(size)))


def _text(rng: random.Random, alphabet: str, size: int = 200) -> str:
    return "".join(rng.choice(alphabet) for _ in range(rng.randrange(size)))


def test_mqtt_publish_parsing():
    rng = random.Random(1)
    client = mqtt_lite.Client(mqtt_lite.Options("h", 1, False, 1.0), max_packet=4096)
    for _ in range(ROUNDS):
        kind = 0x30 | rng.randrange(16)
        try:
            message = client._parse_publish(kind, _bytes(rng))
        except mqtt_lite.MqttError:
            continue
        assert isinstance(message.topic, str)


def test_mqtt_packet_framing():
    rng = random.Random(2)
    for _ in range(ROUNDS // 3):
        ours, theirs = socket.socketpair()
        try:
            client = mqtt_lite.Client(mqtt_lite.Options("h", 1, False, 0.05), max_packet=512)
            client._sock = ours
            theirs.sendall(_bytes(rng, 32))
            theirs.shutdown(socket.SHUT_WR)
            try:
                client._read_packet(time.monotonic() + 0.05)
            except (mqtt_lite.MqttError, TimeoutError):
                pass  # The expected outcomes for garbage; anything else fails the test.
        finally:
            ours.close()
            theirs.close()


def test_influx_csv_parsing():
    rng = random.Random(3)
    alphabet = ',"#\r\n _abcdeflrorvtu0123456789'
    fixed = ["#datatype,string,long", ",result,table,_value", "error,reference", ",_result,0,1"]
    for _ in range(ROUNDS):
        text = "\r\n".join(rng.choice([*fixed, _text(rng, alphabet, 40)]) for _ in range(rng.randrange(8)))
        try:
            rows = ming.parse_csv(text, 50)
        except http_lite.HttpError:
            continue
        assert len(rows) <= 50 and all(isinstance(row, dict) for row in rows)


def test_bridge_line_filter():
    rng = random.Random(4)
    alphabet = '{}[]":,.-+eE0123456789 NaInfinityntrufals\\'
    for _ in range(ROUNDS):
        result = bridge._json_object("{" + _text(rng, alphabet, 60))
        if result is not None:
            # Whatever passes must survive the strict re-serialisation the bridge does.
            json.dumps(result["json"], allow_nan=False)


def test_crash_report_parsing():
    rng = random.Random(5)
    alphabet = "Guru Meditation Error: Backtrace:0x40 MEPCRAabort() was called at PC:x\n0123456789abcdef"
    for _ in range(ROUNDS):
        try:
            result = crash.parse(_text(rng, alphabet, 300))
        except ToolError:
            continue
        assert result is None or isinstance(result, dict)


def test_fingerprint_banner_analysis():
    rng = random.Random(6)
    for _ in range(ROUNDS):
        banner = rng.choice([b"ets Jun  8 2016\r\n", b"ESP-ROM:esp32s3\r\n", b'{"fw":', b"MicroPython v1", b""])
        result = fingerprint.analyse(banner + _bytes(rng, 200))
        assert isinstance(result["matches"], list)


def test_micropython_listing_parsing():
    rng = random.Random(7)
    alphabet = '[]",0123456789 dirfile/\n'
    for _ in range(ROUNDS):
        try:
            listing = micropython.parse_listing(_text(rng, alphabet, 120))
        except ToolError:
            continue
        assert isinstance(listing, list)


class _RandomModbus:
    """Echoes the request's transaction header, then sends a random PDU."""

    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen()
        self.port = self.server.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            with conn:
                request = conn.recv(12)
                if len(request) < 12:
                    continue
                tid, _, _, unit = struct.unpack(">HHHB", request[:7])
                function = request[7]
                pdu = bytes([self.rng.choice([function, function | 0x80, self.rng.getrandbits(8)])])
                pdu += _bytes(self.rng, 12)
                conn.sendall(struct.pack(">HHHB", tid, 0, len(pdu) + 1, unit) + pdu)


def test_modbus_reply_parsing(monkeypatch):
    fake = _RandomModbus(8)
    monkeypatch.setattr(weintek, "TIMEOUT_SECONDS", 1.0)
    target = WeintekModbusTarget("127.0.0.1", fake.port, 1, (ModbusRange("LW", 0, 4), ModbusRange("LB", 0, 8)))
    try:
        for n in range(ROUNDS // 3):
            area, count = ("LW", 2) if n % 2 else ("LB", 5)
            try:
                values = weintek.modbus_read(target, area, 0, count)
            except ToolError:
                continue
            assert len(values) == count
    finally:
        fake.server.close()


@pytest.mark.parametrize("seed", range(3))
def test_http_error_detail_never_escapes(seed):
    rng = random.Random(100 + seed)
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()

    def answer():
        for _ in range(20):
            try:
                conn, _ = server.accept()
            except OSError:
                return
            with conn:
                conn.recv(4096)
                head = rng.choice([b"HTTP/1.1 200 OK\r\nContent-Length: 50\r\n\r\n", b"HTTP/1.1 500 X\r\n\r\n", b""])
                conn.sendall(head + _bytes(rng, 80))

    threading.Thread(target=answer, daemon=True).start()
    try:
        for _ in range(20):
            try:
                http_lite.request("GET", f"http://127.0.0.1:{server.getsockname()[1]}/", timeout=1)
            except http_lite.HttpError:
                pass  # The expected outcome for garbage; anything else fails the test.
    finally:
        server.close()
