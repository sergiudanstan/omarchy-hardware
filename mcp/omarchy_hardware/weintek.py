"""Weintek cMT/MT HMI clients.

MQTT publish and subscribe on exact, allowlisted topics. The target handed in is the one policy.check_weintek_mqtt
returned, so its transport security has already been validated: TLS unless that
exact target carries allow_insecure. Credentials are named in the config and read
at connect time, never stored or returned.
"""

from __future__ import annotations

from typing import Any

from . import errors, mqtt_lite
from .config import WeintekMqttTarget
from .errors import ToolError
from .ming import read_secret

TIMEOUT_SECONDS = 10
# A tag value, not a file transfer. Anything larger is refused before connecting.
MAX_PAYLOAD_BYTES = 4096
MAX_LISTEN_SECONDS = 30
MAX_MESSAGES = 100


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
