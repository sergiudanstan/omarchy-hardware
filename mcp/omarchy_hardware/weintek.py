"""Weintek cMT/MT HMI clients.

MQTT publish only, for now. The target handed in is the one policy.check_weintek_mqtt
returned, so its transport security has already been validated: TLS unless that
exact target carries allow_insecure. Credentials are named in the config and read
at connect time, never stored or returned.
"""

from __future__ import annotations

from . import errors, mqtt_lite
from .config import WeintekMqttTarget
from .errors import ToolError
from .ming import read_secret

TIMEOUT_SECONDS = 10
# A tag value, not a file transfer. Anything larger is refused before connecting.
MAX_PAYLOAD_BYTES = 4096


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


def mqtt_publish(target: WeintekMqttTarget, topic: str, payload: bytes, *, qos: int, retain: bool) -> None:
    max_packet = MAX_PAYLOAD_BYTES + 2 + len(topic.encode()) + 2
    try:
        with mqtt_lite.Client(_options(target), max_packet=max_packet) as client:
            client.publish(topic, payload, qos=qos, retain=retain)
    except mqtt_lite.MqttError as exc:
        text = str(exc)
        code = errors.SERVICE_UNREACHABLE if text.startswith("could not connect") else errors.SERVICE_ERROR
        hint = "Check the HMI or broker is reachable and the [[weintek.mqtt]] security settings match it."
        if "user name or password" in text or "not authorized" in text:
            hint = "Check the username and password named under its security table, and the broker's ACL."
        raise ToolError(code, f"{_label(target)}: {text}.", hint) from None

