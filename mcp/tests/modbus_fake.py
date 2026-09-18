"""A Modbus TCP server holding HMI memory, laid out as EasyBuilder Pro's MODBUS Server driver serves it."""

from __future__ import annotations

import socket
import struct
import threading


class FakeModbusServer:
    def __init__(self, *, unit: int = 1, holding: dict[int, int] | None = None, coils: dict[int, int] | None = None,
                 exception: int | None = None) -> None:
        self.unit = unit
        self.holding = holding or {}
        self.coils = coils or {}
        self.exception = exception
        self.requests: list[tuple[int, int, int]] = []
        self._server = socket.socket()
        self._server.bind(("127.0.0.1", 0))
        self._server.listen()
        self.port = self._server.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def close(self) -> None:
        self._server.close()

    def _accept(self) -> None:
        while True:
            try:
                conn, _ = self._server.accept()
            except OSError:
                return
            with conn:
                request = conn.recv(12)
                if len(request) < 12:
                    continue
                tid, _proto, _length, unit, function, address, count = struct.unpack(">HHHBBHH", request)
                self.requests.append((function, address, count))
                if self.exception is not None:
                    pdu = bytes([function | 0x80, self.exception])
                elif function == 0x03:
                    words = [self.holding.get(address + i, 0) for i in range(count)]
                    pdu = bytes([function, 2 * count]) + struct.pack(f">{count}H", *words)
                else:
                    packed = bytearray((count + 7) // 8)
                    for i in range(count):
                        if self.coils.get(address + i):
                            packed[i // 8] |= 1 << (i % 8)
                    pdu = bytes([function, len(packed)]) + bytes(packed)
                conn.sendall(struct.pack(">HHHB", tid, 0, len(pdu) + 1, unit) + pdu)
