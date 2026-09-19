"""A narrow WebSocket client (RFC 6455) for Slack Socket Mode.

Only what Socket Mode needs: one TLS connection, text messages in both
directions, ping/pong and close. Standard library only, like mqtt_lite and
http_lite, so the Slack bridge adds no dependency to the hash-locked install.

- wss:// only, verified TLS 1.2+. Plain ws:// is accepted for a loopback host
  alone, which the tests use.
- No proxies and no redirects: the URL Slack hands out is the only peer.
- Bounded: headers, single frames and reassembled messages have byte caps, and
  server frames must be unmasked, as the RFC requires.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import os
import socket
import ssl
import struct
import urllib.parse

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_HEADER_BYTES = 16 * 1024
MAX_MESSAGE_BYTES = 1024 * 1024
FRAME_TIMEOUT = 15.0

OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


class WsError(Exception):
    """The connection failed or the peer broke the protocol."""


class Closed(WsError):
    """The peer closed the connection."""


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class Client:
    def __init__(self, url: str, *, timeout: float = 15.0, ca_file: str | None = None) -> None:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("wss", "ws") or not parts.hostname:
            raise WsError("refusing a URL that is not wss://")
        if parts.scheme == "ws" and not _loopback(parts.hostname):
            raise WsError("plain ws:// is only allowed to a loopback address")
        self.host = parts.hostname
        port = parts.port or (443 if parts.scheme == "wss" else 80)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        self._buffer = b""
        try:
            raw = socket.create_connection((self.host, port), timeout=timeout)
        except OSError as exc:
            raise WsError(f"could not connect ({type(exc).__name__})") from None
        if parts.scheme == "wss":
            context = ssl.create_default_context(cafile=ca_file)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            try:
                raw = context.wrap_socket(raw, server_hostname=self.host)
            except (OSError, ssl.SSLError) as exc:
                raw.close()
                raise WsError(f"TLS failed ({type(exc).__name__})") from None
        self.sock = raw
        self._handshake(path, port, parts.scheme)

    # ------------------------------------------------------------------ plumbing

    def _recv_exact(self, count: int) -> bytes:
        while len(self._buffer) < count:
            try:
                chunk = self.sock.recv(max(4096, count - len(self._buffer)))
            except TimeoutError:
                raise
            except OSError as exc:
                raise WsError(f"connection lost ({type(exc).__name__})") from None
            if not chunk:
                raise Closed("the server closed the connection")
            self._buffer += chunk
        data, self._buffer = self._buffer[:count], self._buffer[count:]
        return data

    def _handshake(self, path: str, port: int, scheme: str) -> None:
        key = base64.b64encode(os.urandom(16)).decode()
        default_port = 443 if scheme == "wss" else 80
        host = self.host if port == default_port else f"{self.host}:{port}"
        request = (
            f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode())
        while b"\r\n\r\n" not in self._buffer:
            if len(self._buffer) > MAX_HEADER_BYTES:
                raise WsError("the handshake response is too large")
            chunk = self.sock.recv(4096)
            if not chunk:
                raise Closed("the server closed the connection during the handshake")
            self._buffer += chunk
        head, self._buffer = self._buffer.split(b"\r\n\r\n", 1)
        lines = head.decode("latin-1").split("\r\n")
        if not lines[0].startswith("HTTP/1.1 101"):
            raise WsError(f"the server refused the upgrade ({lines[0][:60]})")
        headers = {}
        for line in lines[1:]:
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
        expected = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()  # noqa: S324 - RFC 6455
        if headers.get("sec-websocket-accept") != expected:
            raise WsError("the server's handshake answer is wrong")

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        header = bytes([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header += bytes([0x80 | length])
        elif length < 1 << 16:
            header += bytes([0x80 | 126]) + struct.pack("!H", length)
        else:
            header += bytes([0x80 | 127]) + struct.pack("!Q", length)
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        try:
            self.sock.sendall(header + mask + masked)
        except OSError as exc:
            raise WsError(f"send failed ({type(exc).__name__})") from None

    def _read_frame(self) -> tuple[bool, int, bytes]:
        first, second = self._recv_exact(2)
        # The frame has started: finish it under a fixed deadline, because giving up
        # halfway would leave the stream out of step.
        self.sock.settimeout(FRAME_TIMEOUT)
        try:
            return self._read_rest(first, second)
        except TimeoutError:
            raise WsError("a frame stalled halfway") from None

    def _read_rest(self, first: int, second: int) -> tuple[bool, int, bytes]:
        fin, opcode = bool(first & 0x80), first & 0x0F
        if first & 0x70:
            raise WsError("the server set reserved bits")
        if second & 0x80:
            raise WsError("the server masked a frame")
        length = second & 0x7F
        if length == 126:
            (length,) = struct.unpack("!H", self._recv_exact(2))
        elif length == 127:
            (length,) = struct.unpack("!Q", self._recv_exact(8))
        if length > MAX_MESSAGE_BYTES:
            raise WsError("a frame exceeds the size limit")
        return fin, opcode, self._recv_exact(length)

    # ------------------------------------------------------------------ public

    def send_text(self, text: str) -> None:
        self._send_frame(OP_TEXT, text.encode("utf-8"))

    def recv_text(self, timeout: float | None = None) -> str | None:
        """The next text message, or None if nothing complete arrived within timeout."""
        message = b""
        started = False
        try:
            while True:
                self.sock.settimeout(timeout)
                fin, opcode, payload = self._read_frame()
                if opcode == OP_PING:
                    self._send_frame(OP_PONG, payload)
                    continue
                if opcode == OP_PONG:
                    continue
                if opcode == OP_CLOSE:
                    try:
                        self._send_frame(OP_CLOSE, payload[:2])
                    except WsError:
                        pass
                    raise Closed("the server closed the connection")
                if opcode in (OP_TEXT, OP_BINARY):
                    if started:
                        raise WsError("a new message started inside a fragmented one")
                    started, message = True, payload
                elif opcode == OP_CONT:
                    if not started:
                        raise WsError("a continuation frame arrived with no message")
                    message += payload
                else:
                    raise WsError(f"unknown opcode {opcode}")
                if len(message) > MAX_MESSAGE_BYTES:
                    raise WsError("a message exceeds the size limit")
                if fin:
                    return message.decode("utf-8", errors="replace")
        except TimeoutError:
            if started:
                raise WsError("a fragmented message stalled") from None
            return None

    def close(self) -> None:
        try:
            self._send_frame(OP_CLOSE, struct.pack("!H", 1000))
        except WsError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
