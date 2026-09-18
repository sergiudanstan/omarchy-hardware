"""In-process stand-ins for an MQTT broker and the MING stack's HTTP APIs.

Both bind to 127.0.0.1 on an ephemeral port and record what they receive, so a
test can assert on the exact bytes the client sent.
"""

from __future__ import annotations

import json
import socket
import ssl
import struct
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def wait_for(predicate: Callable[[], bool], seconds: float = 2.0) -> bool:
    """The fakes record on their own threads; give them a moment to catch up."""
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.005)
    return True


def _length(value: int) -> bytes:
    out = bytearray()
    while True:
        byte, value = value % 128, value // 128
        out.append(byte | 0x80 if value else byte)
        if not value:
            return bytes(out)


def _string(value: str) -> bytes:
    raw = value.encode()
    return struct.pack("!H", len(raw)) + raw


def publish_packet(topic: str, payload: bytes, *, retain: bool = False) -> bytes:
    body = _string(topic) + payload
    return bytes([0x30 | (0x01 if retain else 0)]) + _length(len(body)) + body


class FakeBroker:
    """Speaks just enough MQTT 3.1.1 to exercise mqtt_lite."""

    def __init__(
        self,
        *,
        connack: int = 0,
        retained: tuple[tuple[str, bytes], ...] = (),
        deliver: tuple[tuple[str, bytes], ...] = (),
        suback: int = 0,
        raw_after_suback: bytes = b"",
        ack_publish: bool = True,
        tls: ssl.SSLContext | None = None,
    ) -> None:
        self.tls = tls
        self.connack = connack
        self.retained = retained
        self.deliver = deliver
        self.suback = suback
        self.raw_after_suback = raw_after_suback
        self.ack_publish = ack_publish
        self.connects: list[dict] = []
        self.subscriptions: list[str] = []
        self.published: list[dict] = []
        self.disconnects = 0
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen()
        self.port = self._server.getsockname()[1]
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._server.close()

    def _accept(self) -> None:
        while True:
            try:
                conn, _ = self._server.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    @staticmethod
    def _read(conn: socket.socket) -> tuple[int, bytes] | None:
        head = conn.recv(1)
        if not head:
            return None
        length, shift = 0, 0
        while True:
            byte = conn.recv(1)[0]
            length |= (byte & 0x7F) << shift
            shift += 7
            if not byte & 0x80:
                break
        body = b""
        while len(body) < length:
            chunk = conn.recv(length - len(body))
            if not chunk:
                return None
            body += chunk
        return head[0], body

    def _handle(self, conn: socket.socket) -> None:
        if self.tls is not None:
            try:
                conn = self.tls.wrap_socket(conn, server_side=True)
            except (OSError, ssl.SSLError):
                conn.close()
                return
        with conn:
            try:
                while True:
                    packet = self._read(conn)
                    if packet is None:
                        return
                    kind, body = packet
                    if kind & 0xF0 == 0x10:
                        self._on_connect(conn, body)
                        if self.connack:
                            return
                    elif kind & 0xF0 == 0x80:
                        self._on_subscribe(conn, body)
                    elif kind & 0xF0 == 0x30:
                        self._on_publish(conn, kind, body)
                    elif kind & 0xF0 == 0xE0:
                        self.disconnects += 1
                        return
            except OSError:
                return

    def _on_connect(self, conn: socket.socket, body: bytes) -> None:
        flags = body[7]
        offset = 10
        fields = []
        while offset < len(body):
            size = struct.unpack("!H", body[offset:offset + 2])[0]
            fields.append(body[offset + 2:offset + 2 + size].decode())
            offset += 2 + size
        record = {"client_id": fields[0], "clean": bool(flags & 0x02), "protocol": body[2:6], "level": body[6]}
        index = 1
        if flags & 0x80:
            record["username"] = fields[index]
            index += 1
        if flags & 0x40:
            record["password"] = fields[index]
        self.connects.append(record)
        conn.sendall(bytes([0x20, 2, 0, self.connack]))

    def _on_subscribe(self, conn: socket.socket, body: bytes) -> None:
        packet_id = body[:2]
        size = struct.unpack("!H", body[2:4])[0]
        self.subscriptions.append(body[4:4 + size].decode())
        for topic, payload in self.retained:
            conn.sendall(publish_packet(topic, payload, retain=True))
        conn.sendall(bytes([0x90, 3]) + packet_id + bytes([self.suback]))
        for topic, payload in self.deliver:
            conn.sendall(publish_packet(topic, payload))
        if self.raw_after_suback:
            conn.sendall(self.raw_after_suback)

    def _on_publish(self, conn: socket.socket, kind: int, body: bytes) -> None:
        qos = (kind >> 1) & 0x03
        size = struct.unpack("!H", body[:2])[0]
        topic = body[2:2 + size].decode()
        offset = 2 + size
        packet_id = b""
        if qos:
            packet_id = body[offset:offset + 2]
            offset += 2
        self.published.append({"topic": topic, "payload": body[offset:], "qos": qos, "retain": bool(kind & 1)})
        if qos == 1 and self.ack_publish:
            conn.sendall(bytes([0x40, 2]) + packet_id)


class FakeHttp:
    """A tiny router: routes map (method, path) to (status, headers, body)."""

    def __init__(
        self,
        routes: dict[tuple[str, str], tuple[int, dict[str, str], bytes]],
        tls: ssl.SSLContext | None = None,
    ) -> None:
        self.routes = routes
        self.requests: list[dict] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def _serve(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                path, _, query = self.path.partition("?")
                fake.requests.append(
                    {"method": self.command, "path": path, "query": query, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body}
                )
                status, headers, payload = fake.routes.get((self.command, path), (404, {}, b'{"message":"nope"}'))
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST = _serve

            def log_message(self, *args: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        scheme = "http"
        if tls is not None:
            self._server.socket = tls.wrap_socket(self._server.socket, server_side=True)
            scheme = "https"
        self.url = f"{scheme}://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def json_route(status: int, value: object) -> tuple[int, dict[str, str], bytes]:
    return status, {"Content-Type": "application/json"}, json.dumps(value).encode()


def make_pki(directory, san: str = "DNS:localhost,IP:127.0.0.1"):
    """A CA and a server certificate, made with the same openssl commands as examples/ming-stack/bootstrap.sh."""
    import subprocess

    def run(*args: str) -> None:
        subprocess.run(["openssl", *args], cwd=directory, check=True, capture_output=True)  # noqa: S603, S607

    run("req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-days", "2",
        "-subj", "/CN=test CA", "-keyout", "ca.key", "-out", "ca.crt",
        "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign")
    run("req", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-subj", "/CN=server",
        "-keyout", "server.key", "-out", "server.csr")
    (directory / "ext.cnf").write_text(
        f"subjectAltName={san}\nextendedKeyUsage=serverAuth\nbasicConstraints=critical,CA:FALSE\n"
    )
    run("x509", "-req", "-in", "server.csr", "-CA", "ca.crt", "-CAkey", "ca.key", "-CAcreateserial",
        "-days", "2", "-out", "server.crt", "-extfile", "ext.cnf")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(directory / "server.crt", directory / "server.key")
    return directory / "ca.crt", context
