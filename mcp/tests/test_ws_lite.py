"""ws_lite against a tiny in-process WebSocket server on loopback."""

import base64
import hashlib
import socket
import struct
import threading

import pytest

from omarchy_hardware import ws_lite


def _frame(opcode, payload, fin=True, masked=False):
    head = bytes([(0x80 if fin else 0) | opcode])
    length = len(payload)
    mask_bit = 0x80 if masked else 0
    if length < 126:
        head += bytes([mask_bit | length])
    elif length < 65536:
        head += bytes([mask_bit | 126]) + struct.pack("!H", length)
    else:
        head += bytes([mask_bit | 127]) + struct.pack("!Q", length)
    if masked:
        mask = b"\x01\x02\x03\x04"
        return head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return head + payload


def _read_client_frame(conn):
    first, second = conn.recv(1)[0], conn.recv(1)[0]
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", conn.recv(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", conn.recv(8))[0]
    mask = conn.recv(4)
    data = b""
    while len(data) < length:
        data += conn.recv(length - len(data))
    assert second & 0x80, "client frames must be masked"
    return first & 0x0F, bytes(b ^ mask[i % 4] for i, b in enumerate(data))


class Server:
    def __init__(self, script, accept_override=None):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.script = script
        self.accept_override = accept_override
        self.received = []
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        conn, _ = self.sock.accept()
        request = b""
        while b"\r\n\r\n" not in request:
            request += conn.recv(1024)
        lines = request.decode().split("\r\n")
        key = [line.split(": ", 1)[1] for line in lines if line.startswith("Sec-WebSocket-Key")][0]
        accept = self.accept_override or base64.b64encode(
            hashlib.sha1((key + ws_lite.GUID).encode()).digest()).decode()  # noqa: S324
        conn.sendall(f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                     f"Sec-WebSocket-Accept: {accept}\r\n\r\n".encode())
        self.script(self, conn)
        conn.close()


def _client(server, **kwargs):
    return ws_lite.Client(f"ws://127.0.0.1:{server.port}/link?ticket=abc", **kwargs)


def test_text_both_ways_and_fragmentation():
    def script(server, conn):
        conn.sendall(_frame(ws_lite.OP_TEXT, b'{"type":"hello"}'))
        conn.sendall(_frame(ws_lite.OP_TEXT, b"frag", fin=False) + _frame(ws_lite.OP_CONT, b"mented"))
        server.received.append(_read_client_frame(conn))

    server = Server(script)
    client = _client(server)
    assert client.recv_text(timeout=2) == '{"type":"hello"}'
    assert client.recv_text(timeout=2) == "fragmented"
    client.send_text('{"envelope_id":"e1"}')
    server.thread.join(2)
    assert server.received == [(ws_lite.OP_TEXT, b'{"envelope_id":"e1"}')]
    client.close()


def test_ping_is_answered_and_long_messages_work():
    big = b"x" * 70000

    def script(server, conn):
        conn.sendall(_frame(ws_lite.OP_PING, b"hb"))
        server.received.append(_read_client_frame(conn))
        conn.sendall(_frame(ws_lite.OP_TEXT, big))

    server = Server(script)
    client = _client(server)
    assert client.recv_text(timeout=2) == big.decode()
    assert server.received == [(ws_lite.OP_PONG, b"hb")]
    client.close()


def test_silence_returns_none_and_close_raises():
    event = threading.Event()

    def script(server, conn):
        event.wait(2)
        conn.sendall(_frame(ws_lite.OP_CLOSE, struct.pack("!H", 1000)))
        server.received.append(_read_client_frame(conn))

    server = Server(script)
    client = _client(server)
    assert client.recv_text(timeout=0.2) is None
    event.set()
    with pytest.raises(ws_lite.Closed):
        client.recv_text(timeout=2)
    server.thread.join(2)
    assert server.received[0][0] == ws_lite.OP_CLOSE


@pytest.mark.parametrize(
    "bad",
    [
        _frame(ws_lite.OP_TEXT, b"hi", masked=True),
        bytes([0x80 | 0x40 | ws_lite.OP_TEXT, 2]) + b"hi",
        _frame(ws_lite.OP_CONT, b"orphan"),
        bytes([0x80 | ws_lite.OP_TEXT, 127]) + struct.pack("!Q", ws_lite.MAX_MESSAGE_BYTES + 1),
        _frame(0x3, b"?"),
    ],
)
def test_protocol_violations_are_errors(bad):
    def script(server, conn):
        conn.sendall(bad)

    server = Server(script)
    client = _client(server)
    with pytest.raises(ws_lite.WsError):
        client.recv_text(timeout=2)
    client.close()


def test_wrong_handshake_answer_is_refused():
    server = Server(lambda s, c: None, accept_override="bm9wZQ==")
    with pytest.raises(ws_lite.WsError, match="handshake"):
        _client(server)


@pytest.mark.parametrize("url", ["ws://example.com/x", "http://127.0.0.1/", "wss:///nohost", "ftp://x"])
def test_only_wss_or_loopback_ws(url):
    with pytest.raises(ws_lite.WsError):
        ws_lite.Client(url, timeout=1)
