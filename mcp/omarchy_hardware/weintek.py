"""Weintek cMT/MT HMI clients.

MQTT publish and subscribe on exact, allowlisted topics, and Modbus TCP reads of
HMI memory served by EasyBuilder Pro's MODBUS Server driver. The target handed in is the one policy.check_weintek_mqtt
returned, so its transport security has already been validated: TLS unless that
exact target carries allow_insecure. Credentials are named in the config and read
at connect time, never stored or returned.
"""

from __future__ import annotations

import socket
import struct
import time
from typing import Any

from . import errors, mqtt_lite
from .config import WeintekModbusTarget, WeintekMqttTarget
from .errors import ToolError
from .ming import read_secret

TIMEOUT_SECONDS = 10
# A tag value, not a file transfer. Anything larger is refused before connecting.
MAX_PAYLOAD_BYTES = 4096
MAX_LISTEN_SECONDS = 30
MAX_MESSAGES = 100
# Well inside Modbus's own per-request limits (125 registers, 2000 coils).
MAX_MODBUS_WORDS = 64
MAX_MODBUS_BITS = 256


def _label(target: WeintekMqttTarget) -> str:
    return f"Weintek MQTT {target.host}:{target.port}"


def _options(target: WeintekMqttTarget) -> mqtt_lite.Options:
    security = target.security
    return mqtt_lite.Options(
        host=target.host,
        port=target.port,
        tls=security.tls,
        timeout=float(TIMEOUT_SECONDS),
        ca_file=security.ca_file,
        client_certificate=security.client_certificate,
        client_key=security.client_key,
        username=security.username,
        password=read_secret(security.password_env, security.password_file, _label(target)),
    )


def _failure(target: WeintekMqttTarget, exc: mqtt_lite.MqttError) -> ToolError:
    text = str(exc)
    code = errors.SERVICE_UNREACHABLE if text.startswith("could not connect") else errors.SERVICE_ERROR
    hint = "Check the HMI or broker is reachable and the [[weintek.mqtt]] security settings match it."
    if "user name or password" in text or "not authorized" in text:
        hint = "Check the username and password named under its security table, and the broker's ACL."
    return ToolError(code, f"{_label(target)}: {text}.", hint)


def _max_packet(topic: str) -> int:
    return MAX_PAYLOAD_BYTES + 2 + len(topic.encode()) + 2


def mqtt_publish(target: WeintekMqttTarget, topic: str, payload: bytes, *, qos: int, retain: bool) -> None:
    try:
        with mqtt_lite.Client(_options(target), max_packet=_max_packet(topic)) as client:
            client.publish(topic, payload, qos=qos, retain=retain)
    except mqtt_lite.MqttError as exc:
        raise _failure(target, exc) from None


def mqtt_subscribe(target: WeintekMqttTarget, topic: str, *, seconds: int, max_messages: int) -> dict[str, Any]:
    """Listen on one exact topic; the retained value, if any, arrives first."""
    try:
        with mqtt_lite.Client(_options(target), max_packet=_max_packet(topic)) as client:
            client.subscribe(topic)
            received, stopped = client.collect(seconds, max_messages)
    except mqtt_lite.MqttError as exc:
        raise _failure(target, exc) from None

    messages = []
    for message in received:
        # An exact topic was asked for; anything else the broker sends is dropped.
        if message.topic != topic:
            continue
        entry: dict[str, Any] = {"retained": message.retain, "qos": message.qos, "bytes": len(message.payload)}
        try:
            entry.update(payload=message.payload.decode("utf-8"), encoding="utf-8")
        except UnicodeDecodeError:
            entry.update(payload=message.payload.hex(), encoding="hex")
        messages.append(entry)
    return {"messages": messages, "stopped": stopped}


# --------------------------------------------------------------------------- Modbus TCP

READ_COILS = 0x01
READ_HOLDING = 0x03
MODBUS_EXCEPTIONS = {
    1: "illegal function",
    2: "illegal data address",
    3: "illegal data value",
    4: "server device failure",
    6: "server device busy",
    10: "gateway path unavailable",
    11: "gateway target failed to respond",
}


def modbus_address(area: str, start: int) -> tuple[int, int]:
    """Function code and protocol address for an HMI address, per EasyBuilder Pro's mapping."""
    if area == "LB":
        return READ_COILS, start
    if area == "LW":
        return READ_HOLDING, start
    # RW-0 is 4x 10000, which is protocol address 9999.
    return READ_HOLDING, 9_999 + start


def _recv_exact(conn: socket.socket, count: int, deadline: float) -> bytes:
    data = b""
    while len(data) < count:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        conn.settimeout(remaining)
        chunk = conn.recv(count - len(data))
        if not chunk:
            raise ConnectionError("connection closed")
        data += chunk
    return data


def modbus_read(target: WeintekModbusTarget, area: str, start: int, count: int) -> list[int]:
    function, address = modbus_address(area, start)
    label = f"Weintek Modbus {target.host}:{target.port}"
    transaction = int(time.monotonic() * 1000) & 0xFFFF
    request = struct.pack(">HHHBBHH", transaction, 0, 6, target.unit, function, address, count)
    deadline = time.monotonic() + TIMEOUT_SECONDS
    try:
        with socket.create_connection((target.host, target.port), timeout=TIMEOUT_SECONDS) as conn:
            conn.sendall(request)
            header = _recv_exact(conn, 7, deadline)
            tid, protocol, length, unit = struct.unpack(">HHHB", header)
            if tid != transaction or protocol != 0 or unit != target.unit or not 2 <= length <= 260:
                raise ToolError(errors.SERVICE_ERROR, f"{label}: the reply does not match the request.")
            pdu = _recv_exact(conn, length - 1, deadline)
    except (OSError, TimeoutError) as exc:
        reason = "timed out" if isinstance(exc, TimeoutError) else type(exc).__name__
        raise ToolError(
            errors.SERVICE_UNREACHABLE,
            f"{label}: could not read ({reason}).",
            "Check the HMI is reachable and its project enables the MODBUS Server driver on this port.",
        ) from None

    if pdu[0] == function | 0x80:
        code = pdu[1] if len(pdu) > 1 else 0
        raise ToolError(
            errors.SERVICE_ERROR,
            f"{label}: Modbus exception {code} ({MODBUS_EXCEPTIONS.get(code, 'unknown')}).",
            "An illegal data address usually means the range is outside what the HMI's MODBUS Server exposes.",
        )
    if pdu[0] != function or len(pdu) < 2 or len(pdu) != 2 + pdu[1]:
        raise ToolError(errors.SERVICE_ERROR, f"{label}: malformed reply.")
    data = pdu[2:]

    if function == READ_COILS:
        if len(data) != (count + 7) // 8:
            raise ToolError(errors.SERVICE_ERROR, f"{label}: the reply has the wrong number of bits.")
        return [(data[i // 8] >> (i % 8)) & 1 for i in range(count)]
    if len(data) != 2 * count:
        raise ToolError(errors.SERVICE_ERROR, f"{label}: the reply has the wrong number of registers.")
    return list(struct.unpack(f">{count}H", data))
