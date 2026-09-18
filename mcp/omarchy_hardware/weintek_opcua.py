"""OPC UA reads and writes against a Weintek HMI's built-in OPC UA server.

EasyBuilder Pro publishes chosen LB/LW and PLC addresses as OPC UA variables
([IIoT] > OPC UA Server). The target handed in is the one
policy.check_weintek_opcua returned, so its security settings have already been
validated; the session is opened with exactly those settings.

asyncua is imported lazily so a broken or missing install only disables these
two tools, not the whole server.
"""

from __future__ import annotations

import asyncio
import math
from typing import Any

from . import errors
from .config import WeintekOpcUaTarget
from .errors import ToolError
from .ming import read_secret

TIMEOUT_SECONDS = 10
MAX_STRING_BYTES = 1024

_INTEGER_RANGES = {
    "SByte": (-(2**7), 2**7 - 1),
    "Byte": (0, 2**8 - 1),
    "Int16": (-(2**15), 2**15 - 1),
    "UInt16": (0, 2**16 - 1),
    "Int32": (-(2**31), 2**31 - 1),
    "UInt32": (0, 2**32 - 1),
    "Int64": (-(2**63), 2**63 - 1),
    "UInt64": (0, 2**64 - 1),
}


def _label(target: WeintekOpcUaTarget) -> str:
    return f"Weintek OPC UA {target.endpoint}"


def coerce(text: str, variant: str) -> Any:
    """Turn the caller's text into a value of the node's own scalar type, or refuse."""
    if variant == "Boolean":
        lowered = text.strip().lower()
        if lowered in ("true", "1"):
            return True
        if lowered in ("false", "0"):
            return False
        raise ToolError(errors.INVALID_ARGUMENT, "This node is Boolean; pass true, false, 1 or 0.")
    if variant in _INTEGER_RANGES:
        low, high = _INTEGER_RANGES[variant]
        try:
            number = int(text.strip(), 10)
        except ValueError:
            raise ToolError(errors.INVALID_ARGUMENT, f"This node is {variant}; pass a whole number.") from None
        if not low <= number <= high:
            raise ToolError(errors.INVALID_ARGUMENT, f"{number} is outside the {variant} range {low}..{high}.")
        return number
    if variant in ("Float", "Double"):
        try:
            number = float(text.strip())
        except ValueError:
            raise ToolError(errors.INVALID_ARGUMENT, f"This node is {variant}; pass a number.") from None
        if not math.isfinite(number):
            raise ToolError(errors.INVALID_ARGUMENT, "NaN and infinity are not written to equipment.")
        return number
    if variant == "String":
        if len(text.encode("utf-8")) > MAX_STRING_BYTES:
            raise ToolError(errors.WRITE_TOO_LARGE, f"Strings are limited to {MAX_STRING_BYTES} bytes.")
        return text
    raise ToolError(
        errors.UNSUPPORTED_OPERATION,
        f"Writing a {variant} node is not supported.",
        "Only scalar Boolean, integer, Float, Double and String nodes can be written.",
    )


def _render(value: Any) -> Any:
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    return str(value)


async def _session(target: WeintekOpcUaTarget):  # noqa: ANN202 - an asyncua Client
    from asyncua import Client, ua
    from asyncua.crypto import security_policies

    security = target.security
    client = Client(url=target.endpoint, timeout=TIMEOUT_SECONDS)
    if security.mode != "None":
        policy = {
            "Basic256Sha256": security_policies.SecurityPolicyBasic256Sha256,
            "Aes128_Sha256_RsaOaep": security_policies.SecurityPolicyAes128Sha256RsaOaep,
            "Aes256_Sha256_RsaPss": security_policies.SecurityPolicyAes256Sha256RsaPss,
        }[security.policy]
        await client.set_security(
            policy,
            certificate=security.certificate,
            private_key=security.private_key,
            # Pinning the HMI's certificate: a channel to any other server fails.
            server_certificate=security.trust_list,
            mode=getattr(ua.MessageSecurityMode, security.mode),
        )
    if security.username:
        client.set_user(security.username)
        client.set_password(read_secret(security.password_env, None, _label(target)) or "")
    return client


async def _read(target: WeintekOpcUaTarget, node_id: str) -> dict[str, Any]:
    client = await _session(target)
    async with client:
        data = await client.get_node(node_id).read_data_value()
    variant = data.Value.VariantType.name if data.Value is not None else None
    return {
        "value": _render(data.Value.Value if data.Value is not None else None),
        "type": variant,
        "status": data.StatusCode.name if data.StatusCode is not None else None,
        "source_time": data.SourceTimestamp.isoformat() if data.SourceTimestamp else None,
    }


async def _write(target: WeintekOpcUaTarget, node_id: str, text: str) -> dict[str, Any]:
    from asyncua import ua

    client = await _session(target)
    async with client:
        node = client.get_node(node_id)
        current = await node.read_data_value()
        if current.Value is None or isinstance(current.Value.Value, (list, tuple)):
            raise ToolError(errors.UNSUPPORTED_OPERATION, "Only scalar nodes can be written.")
        variant = current.Value.VariantType
        value = coerce(text, variant.name)
        await node.write_value(ua.DataValue(ua.Variant(value, variant)))
        after = await node.read_data_value()
    return {
        "previous": _render(current.Value.Value),
        "value": _render(after.Value.Value if after.Value is not None else None),
        "type": variant.name,
    }


def _run(target: WeintekOpcUaTarget, coroutine_factory) -> dict[str, Any]:  # noqa: ANN001
    try:
        import asyncua  # noqa: F401
    except ImportError:
        raise ToolError(
            errors.UNSUPPORTED_OPERATION,
            "The OPC UA client library (asyncua) is not installed.",
            "Re-run bin/setup.sh to install the pinned dependencies.",
        ) from None
    try:
        return asyncio.run(asyncio.wait_for(coroutine_factory(), TIMEOUT_SECONDS * 2))
    except ToolError:
        raise
    except (TimeoutError, OSError) as exc:
        raise ToolError(
            errors.SERVICE_UNREACHABLE,
            f"{_label(target)}: could not connect ({type(exc).__name__}).",
            "Check the HMI is reachable and its project enables the OPC UA Server on this port.",
        ) from None
    except Exception as exc:  # asyncua raises its own hierarchy for status codes
        name = getattr(exc, "name", None) or type(exc).__name__
        raise ToolError(
            errors.SERVICE_ERROR,
            f"{_label(target)}: {name}.",
            "BadNodeIdUnknown means the node id is not published by the HMI; "
            "BadUserAccessDenied or BadSecurityChecksFailed point at credentials or certificates.",
        ) from None


def read(target: WeintekOpcUaTarget, node_id: str) -> dict[str, Any]:
    return _run(target, lambda: _read(target, node_id))


def write(target: WeintekOpcUaTarget, node_id: str, text: str) -> dict[str, Any]:
    return _run(target, lambda: _write(target, node_id, text))
