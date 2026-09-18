"""A deliberately small MQTT 3.1.1 client: connect, subscribe at QoS 0, publish at QoS 0 or 1.

Written against the standard library because mcp/requirements.lock is hash-pinned
and every new dependency is supply-chain surface. It implements only what the
MING tools need -- one short-lived connection per call, no session state, no
QoS 2 -- and bounds everything it reads: packet sizes, message counts and
wall-clock time.

Reference: MQTT Version 3.1.1, OASIS Standard, section numbers cited inline.
"""

from __future__ import annotations

import contextlib
import os
import socket
import ssl
import struct
import time
from dataclasses import dataclass

CONNECT = 0x10
CONNACK = 0x20
PUBLISH = 0x30
PUBACK = 0x40
SUBSCRIBE = 0x82  # 3.8.1: bits 3-0 of a SUBSCRIBE fixed header are reserved as 0010
SUBACK = 0x90
PINGRESP = 0xD0
DISCONNECT = 0xE0

MAX_REMAINING_LENGTH = 268_435_455  # 2.2.3: four bytes of variable length
CONNACK_REASONS = {
    1: "unacceptable protocol version",
    2: "client identifier rejected",
    3: "server unavailable",
    4: "bad user name or password",
    5: "not authorized",
}


class MqttError(Exception):
    """A protocol, transport or broker refusal. The message never includes credentials."""


@dataclass(frozen=True)
class Message:
    topic: str
    payload: bytes
    qos: int
    retain: bool


def topic_matches(topic_filter: str, topic: str) -> bool:
    """4.7: does `topic` match `topic_filter`? '$' topics never match a leading wildcard."""
    if topic.startswith("$") and topic_filter[:1] in ("+", "#"):
        return False
    filter_levels = topic_filter.split("/")
    topic_levels = topic.split("/")
    for index, level in enumerate(filter_levels):
        if level == "#":
            return True
        if index >= len(topic_levels):
            return False
        if level != "+" and level != topic_levels[index]:
            return False
    return len(filter_levels) == len(topic_levels)


def filter_covers(allowed: str, requested: str) -> bool:
    """Is every topic `requested` can match also matched by `allowed`?

    Used to let a caller narrow an allowlisted filter ('plant/#' -> 'plant/line1/+')
    without ever widening it.
    """
    # 4.7.2: a leading wildcard never matches a '$' topic, so '#' does not cover '$SYS/#'.
    if requested.startswith("$") and allowed[:1] in ("+", "#"):
        return False
    allowed_levels = allowed.split("/")
    requested_levels = requested.split("/")
    for index, level in enumerate(allowed_levels):
        if level == "#":
            return True
        if index >= len(requested_levels):
            return False
        wanted = requested_levels[index]
        if wanted == "#":
            return False
        if level == "+":
            continue
        if wanted != level:
            return False
    return len(allowed_levels) == len(requested_levels)


def _encode_length(length: int) -> bytes:
    if not 0 <= length <= MAX_REMAINING_LENGTH:
        raise MqttError("packet too large for MQTT")
    out = bytearray()
    while True:
        byte, length = length % 128, length // 128
        out.append(byte | 0x80 if length else byte)
        if not length:
            return bytes(out)


def _string(value: str | bytes) -> bytes:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    if len(raw) > 65535:
        raise MqttError("string field longer than 65535 bytes")
    return struct.pack("!H", len(raw)) + raw


def _packet(header: int, body: bytes) -> bytes:
    return bytes([header]) + _encode_length(len(body)) + body


@dataclass(frozen=True)
class Options:
    host: str
    port: int
    tls: bool
    timeout: float
    ca_file: str | None = None
    client_certificate: str | None = None
    client_key: str | None = None
    username: str | None = None
    password: str | None = None


class Client:
    """One connection. Use as a context manager; it always sends DISCONNECT and closes."""

    def __init__(self, options: Options, *, max_packet: int) -> None:
        self._options = options
        self._max_packet = max_packet
        self._sock: socket.socket | None = None
        self._next_id = 1
        # Messages that arrive between SUBSCRIBE and its SUBACK (retained values).
        self._early: list[Message] = []

    # ------------------------------------------------------------------ transport

    def __enter__(self) -> Client:
        self._connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _connect(self) -> None:
        opts = self._options
        context = _tls_context(opts) if opts.tls else None
        try:
            raw = socket.create_connection((opts.host, opts.port), timeout=opts.timeout)
        except OSError as exc:
            raise MqttError(f"could not connect ({type(exc).__name__})") from exc
        try:
            if context is not None:
                self._sock = context.wrap_socket(raw, server_hostname=opts.host)
            else:
                self._sock = raw
            self._handshake()
        except ssl.SSLError as exc:
            raw.close()
            self._sock = None
            raise MqttError(f"TLS handshake failed ({exc.reason or type(exc).__name__})") from exc
        except (OSError, MqttError):
            raw.close()
            self._sock = None
            raise

    def _handshake(self) -> None:
        opts = self._options
        flags = 0x02  # clean session: leave nothing behind on the broker (3.1.2.4)
        payload = _string("omarchy-hw-" + os.urandom(6).hex())
        if opts.username is not None:
            flags |= 0x80
            payload += _string(opts.username)
            if opts.password is not None:
                flags |= 0x40
                payload += _string(opts.password)
        keepalive = max(10, int(opts.timeout) * 2)
        variable = _string("MQTT") + bytes([4, flags]) + struct.pack("!H", keepalive)
        self._send(_packet(CONNECT, variable + payload))

        try:
            kind, body = self._read_packet(time.monotonic() + opts.timeout)
        except TimeoutError:
            raise MqttError("broker did not answer CONNECT in time") from None
        if kind & 0xF0 != CONNACK or len(body) != 2:
            raise MqttError("broker did not answer CONNECT with CONNACK")
        if body[1] != 0:
            raise MqttError(f"broker refused the connection: {CONNACK_REASONS.get(body[1], f'code {body[1]}')}")

    def close(self) -> None:
        if self._sock is None:
            return
        try:
            # Best effort: the broker may already have dropped the connection,
            # and the socket is closed either way.
            with contextlib.suppress(OSError):
                self._sock.sendall(_packet(DISCONNECT, b""))
        finally:
            self._sock.close()
            self._sock = None

    def _connected(self) -> socket.socket:
        if self._sock is None:
            raise MqttError("not connected")
        return self._sock

    def _send(self, data: bytes) -> None:
        sock = self._connected()
        try:
            sock.sendall(data)
        except OSError as exc:
            raise MqttError(f"connection lost while sending ({type(exc).__name__})") from exc

    def _recv_exact(self, count: int, deadline: float) -> bytes:
        sock = self._connected()
        chunks = bytearray()
        while len(chunks) < count:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            sock.settimeout(remaining)
            try:
                chunk = sock.recv(min(65536, count - len(chunks)))
            except TimeoutError:
                raise
            except OSError as exc:
                raise MqttError(f"connection lost while reading ({type(exc).__name__})") from exc
            if not chunk:
                raise MqttError("broker closed the connection")
            chunks += chunk
        return bytes(chunks)

    def _read_packet(self, deadline: float) -> tuple[int, bytes]:
        """Read one packet. Raises TimeoutError at the deadline, MqttError on anything malformed."""
        header = self._recv_exact(1, deadline)[0]
        length = 0
        for shift in (0, 7, 14, 21):
            byte = self._recv_exact(1, deadline)[0]
            length |= (byte & 0x7F) << shift
            if not byte & 0x80:
                break
        else:
            raise MqttError("malformed remaining length")
        if length > self._max_packet:
            # Reading it would mean buffering a size the broker chose. The
            # stream cannot be resynchronised without consuming it, so stop.
            raise MqttError(f"broker sent a {length}-byte packet; the limit is {self._max_packet}")
        return header, self._recv_exact(length, deadline)

    # ------------------------------------------------------------------ operations

    def _packet_id(self) -> int:
        packet_id = self._next_id
        self._next_id = self._next_id % 65535 + 1
        return packet_id

    def subscribe(self, topic_filter: str) -> None:
        """Subscribe at QoS 0 and wait for the SUBACK."""
        packet_id = self._packet_id()
        self._send(_packet(SUBSCRIBE, struct.pack("!H", packet_id) + _string(topic_filter) + b"\x00"))
        deadline = time.monotonic() + self._options.timeout
        while True:
            try:
                kind, body = self._read_packet(deadline)
            except TimeoutError:
                raise MqttError("broker did not acknowledge the subscription") from None
            if kind & 0xF0 == SUBACK:
                if len(body) < 3 or struct.unpack("!H", body[:2])[0] != packet_id:
                    raise MqttError("malformed SUBACK")
                if body[2] == 0x80:
                    raise MqttError("broker refused the subscription (not authorized or invalid filter)")
                return
            # A retained message can legitimately arrive before the SUBACK; the
            # caller asked for it, so it is not dropped. Keep it for collect().
            if kind & 0xF0 == PUBLISH:
                self._early.append(self._parse_publish(kind, body))

    def collect(self, seconds: float, max_messages: int) -> tuple[list[Message], str]:
        """Gather messages for up to `seconds`.

        Returns them with why collection stopped: "timeout", "max_messages", or the
        error that ended the stream. What arrived before an error is still returned.
        """
        messages, self._early = self._early, []
        deadline = time.monotonic() + seconds
        while len(messages) < max_messages:
            try:
                kind, body = self._read_packet(deadline)
            except TimeoutError:
                return messages, "timeout"
            except MqttError as exc:
                return messages, str(exc)
            if kind & 0xF0 == PUBLISH:
                messages.append(self._parse_publish(kind, body))
            # PINGRESP and anything else unsolicited is ignored.
        return messages[:max_messages], "max_messages"

    def _parse_publish(self, kind: int, body: bytes) -> Message:
        qos = (kind >> 1) & 0x03
        if qos == 3 or len(body) < 2:
            raise MqttError("malformed PUBLISH")
        topic_length = struct.unpack("!H", body[:2])[0]
        offset = 2 + topic_length
        if len(body) < offset:
            raise MqttError("malformed PUBLISH")
        try:
            topic = body[2:offset].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MqttError("broker sent a topic that is not UTF-8") from exc
        if qos:
            if len(body) < offset + 2:
                raise MqttError("malformed PUBLISH")
            packet_id = body[offset:offset + 2]
            offset += 2
            if qos == 1:
                self._send(_packet(PUBACK, packet_id))
        return Message(topic=topic, payload=body[offset:], qos=qos, retain=bool(kind & 0x01))

    def publish(self, topic: str, payload: bytes, *, qos: int = 0, retain: bool = False) -> None:
        """Publish; at QoS 1 wait for the broker's PUBACK."""
        if qos not in (0, 1):
            raise MqttError("only QoS 0 and 1 are supported")
        header = PUBLISH | (qos << 1) | (0x01 if retain else 0)
        body = _string(topic)
        packet_id = 0
        if qos:
            packet_id = self._packet_id()
            body += struct.pack("!H", packet_id)
        self._send(_packet(header, body + payload))
        if not qos:
            return
        deadline = time.monotonic() + self._options.timeout
        while True:
            try:
                kind, reply = self._read_packet(deadline)
            except TimeoutError:
                raise MqttError("broker did not acknowledge the publish") from None
            if kind & 0xF0 == PUBACK and reply == struct.pack("!H", packet_id):
                return


def _tls_context(opts: Options) -> ssl.SSLContext:
    """Load the CA and client certificate before connecting, so a bad path is named as such."""
    try:
        context = ssl.create_default_context(cafile=_expand(opts.ca_file))
        if opts.client_certificate and opts.client_key:
            context.load_cert_chain(_expand(opts.client_certificate), _expand(opts.client_key))
    except (OSError, ssl.SSLError) as exc:
        raise MqttError(f"cannot load the configured TLS files ({type(exc).__name__})") from exc
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def _expand(path: str | None) -> str | None:
    return os.path.expanduser(path) if path else None
