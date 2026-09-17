"""MCP server exposing dev-board hardware to Claude.

Every device path passes through policy.resolve_port; every Pi host and pin passes
through the config allowlists. Tools return structured results instead of raising, so
the model always receives an actionable error code.
"""

from __future__ import annotations

import functools
import sys
import traceback
from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from . import __version__, audit, errors, flash, gpio_ssh, jetson_ssh, policy, reference, support
from .boards import enumerate_boards, enumerate_stm32_usb_devices
from .config import Config, ConfigError
from .config import load as load_config
from .errors import ToolError, ok
from .serial_session import MAX_WAIT_MS, SessionManager

mcp = MCPServer(name="omarchy-hardware", version=__version__)

READ_ONLY = ToolAnnotations(read_only_hint=True)
DESTRUCTIVE = ToolAnnotations(destructive_hint=True)
sessions = SessionManager()
_budget: policy.WriteBudget | None = None
_actuation: policy.ActuationBudget | None = None


def _config() -> Config:
    try:
        return load_config()
    except ConfigError as exc:
        raise ToolError(errors.CONFIG_ERROR, str(exc)) from exc


def _write_budget(config: Config) -> policy.WriteBudget:
    global _budget
    if _budget is None or _budget.limit != config.write_budget_bytes_per_min:
        _budget = policy.WriteBudget(config.write_budget_bytes_per_min)
    return _budget


def _actuation_budget(config: Config) -> policy.ActuationBudget:
    global _actuation
    if _actuation is None or _actuation.limit != config.actuation_budget_per_min:
        _actuation = policy.ActuationBudget(config.actuation_budget_per_min)
    return _actuation


def guard(fn: Callable) -> Callable:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return fn(*args, **kwargs)
        except ToolError as exc:
            return exc.as_result()
        except Exception as exc:  # never let a traceback escape as a protocol error
            # The exception *type* is safe to return. The message is not: it
            # routinely carries absolute paths and hostnames, which
            # hardware_report and check_host deliberately keep from the model.
            # Keep the detail on stderr, where the user can read it.
            traceback.print_exc(file=sys.stderr)
            return ToolError(
                type(exc).__name__,
                "The tool failed unexpectedly.",
                "Details were written to the MCP server's stderr log.",
            ).as_result()

    return wrapper


def _decode(data: str, encoding: str) -> bytes:
    if encoding == "hex":
        try:
            return bytes.fromhex(data.replace(" ", ""))
        except ValueError as exc:
            raise ToolError(errors.SERIAL_ERROR, f"Invalid hex payload: {exc}") from exc
    return data.encode("utf-8")


def _encode(payload: bytes, encoding: str) -> str:
    if encoding == "hex":
        return payload.hex()
    return payload.decode("utf-8", errors="replace")


def _resolve_host(host: str | None, config: Config) -> str:
    if host is None:
        if len(config.pi_hosts) == 1:
            return config.pi_hosts[0]
        if not config.pi_hosts:
            raise ToolError(
                errors.HOST_NOT_ALLOWED,
                "No Raspberry Pi hosts are configured.",
                "Add one to [pi] hosts in ~/.config/omarchy-hardware/config.toml.",
            )
        raise ToolError(
            errors.HOST_NOT_ALLOWED,
            "Several Pi hosts are configured; name the one you mean.",
            "Pass host= explicitly. Hostnames are not listed here.",
        )
    return policy.check_host(host, config, hosts=config.pi_hosts, section="[pi] hosts")


def _resolve_jetson_host(host: str | None, config: Config) -> str:
    if host is None:
        if len(config.jetson_hosts) == 1:
            return config.jetson_hosts[0]
        if not config.jetson_hosts:
            raise ToolError(
                errors.HOST_NOT_ALLOWED,
                "No Jetson hosts are configured.",
                "Add one to [jetson] hosts in ~/.config/omarchy-hardware/config.toml.",
            )
        raise ToolError(
            errors.HOST_NOT_ALLOWED,
            "Several Jetson hosts are configured; name the one you mean.",
            "Pass host= explicitly. Hostnames are not listed here.",
        )
    return policy.check_host(host, config, hosts=config.jetson_hosts, section="[jetson] hosts")


MIN_BAUD = 300
MAX_BAUD = 2_000_000


def _require_confirm(confirm: bool, action: str) -> None:
    if not confirm:
        raise ToolError(
            errors.UNCONFIRMED,
            f"Refusing to {action} without confirmation.",
            "Call again with confirm=true once the user agrees.",
        )


def _require_serial_write_target(port: str, config: Config) -> None:
    board = next((item for item in enumerate_boards() if item["port"] == port), None)
    unknown = board is None or board.get("board_type") == "unknown"
    if unknown and not config.allow_unknown_serial:
        raise ToolError(
            errors.UNKNOWN_ADAPTER,
            f"{port} is not a recognised development board.",
            "Set [serial] allow_unknown = true to write to unidentified adapters.",
        )


def _usb_serial_for_compile(port: str | None, fqbn: str) -> str:
    boards = enumerate_boards()
    if port:
        resolved = policy.resolve_port(port)
        for board in boards:
            if board["port"] == resolved:
                return (board.get("serial") or "").strip()
        raise ToolError(errors.PORT_NOT_FOUND, f"{resolved} is not connected.")
    matches = [board for board in boards if board.get("suggested_fqbn") == fqbn]
    if len(matches) == 1:
        return (matches[0].get("serial") or "").strip()
    return ""


# --------------------------------------------------------------------------- boards


@mcp.tool(annotations=READ_ONLY)
@guard
def list_boards() -> dict[str, Any]:
    """List USB serial development boards currently connected to this machine."""
    return ok(boards=enumerate_boards())


@mcp.tool(annotations=READ_ONLY)
@guard
def list_capabilities(family: str | None = None) -> dict[str, Any]:
    """List implemented and unsupported operations for each hardware family."""
    return ok(families=support.export(family))


@mcp.tool(annotations=READ_ONLY)
@guard
def get_hardware_reference(family: str | None = None) -> dict[str, Any]:
    """Describe family capabilities and policy for adapter preparation; not an MHS driver or live discovery."""
    return ok(reference=reference.export_reference(_config(), family))


@mcp.tool(annotations=READ_ONLY)
@guard
def hardware_report() -> dict[str, Any]:
    """Return a redacted local lab report with devices, STM32 probes, sessions and capabilities."""
    config = _config()
    boards = []
    for board in enumerate_boards():
        redacted = dict(board)
        redacted.pop("by_id_path", None)
        if redacted.get("serial"):
            redacted["serial"] = "redacted"
        boards.append(redacted)
    stm32_usb = [{**device, "serial": "redacted"} if device.get("serial") else device
                 for device in enumerate_stm32_usb_devices()]
    return ok(
        devices=boards,
        stm32_usb_devices=stm32_usb,
        sessions=sessions.all(),
        capabilities=support.export(),
        remote_hosts_configured=len(config.pi_hosts),
        jetson_hosts_configured=len(config.jetson_hosts),
    )


@mcp.tool(annotations=READ_ONLY)
@guard
def audit_status() -> dict[str, Any]:
    """Verify the hash chain of the local actuation audit log.

    Reports the first record that does not follow its predecessor, so a rewritten
    or truncated history is visible rather than silently accepted.
    """
    return ok(**audit.verify())


@mcp.tool(annotations=READ_ONLY)
@guard
def describe_board(port: str) -> dict[str, Any]:
    """Describe one connected board in detail, including its suggested FQBN and baud rate."""
    resolved = policy.resolve_port(port)
    for board in enumerate_boards():
        if board["port"] == resolved:
            session = sessions.by_port(resolved)
            return ok(board={**board, "open_session": session.session_id if session else None})
    raise ToolError(errors.PORT_NOT_FOUND, f"{resolved} is not connected.")


# --------------------------------------------------------------------------- serial


@mcp.tool()
@guard
def serial_open(port: str, baud: int = 115200) -> dict[str, Any]:
    """Open a serial session. Idempotent at the same baud; a different baud is refused."""
    if not isinstance(baud, int) or isinstance(baud, bool) or not MIN_BAUD <= baud <= MAX_BAUD:
        raise ToolError(
            errors.SERIAL_ERROR,
            f"Baud must be an integer in {MIN_BAUD}-{MAX_BAUD}.",
        )
    resolved = policy.resolve_port(port)
    policy.check_readable(resolved)
    session = sessions.open(resolved, baud, write_timeout_ms=_config().write_timeout_ms)
    return ok(**session.status())


@mcp.tool(annotations=READ_ONLY)
@guard
def serial_status(session_id: str) -> dict[str, Any]:
    """Report buffered bytes, drops and errors for an open serial session."""
    return ok(**sessions.get(session_id).status())


@mcp.tool(annotations=READ_ONLY)
@guard
def list_sessions() -> dict[str, Any]:
    """List every serial session this server currently holds open."""
    return ok(sessions=sessions.all())


@mcp.tool()
@guard
def serial_read(
    session_id: str,
    max_bytes: int = 4096,
    max_wait_ms: int = 1000,
    until: str | None = None,
    encoding: str = "utf8",
) -> dict[str, Any]:
    """Read buffered serial data.

    Never blocks longer than max_wait_ms (hard-capped at 10s), so a silent device
    cannot hang the call. If `until` is given, waits for that literal terminator.
    """
    result = sessions.get(session_id).read(max_bytes, min(max_wait_ms, MAX_WAIT_MS), until)
    return ok(
        data=_encode(result["data"], encoding),
        encoding=encoding,
        timed_out=result["timed_out"],
        bytes_remaining=result["bytes_remaining"],
        bytes_dropped=result["bytes_dropped"],
    )


@mcp.tool(annotations=DESTRUCTIVE)
@guard
def serial_write(
    session_id: str,
    data: str,
    encoding: str = "utf8",
    append_newline: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Write data to an open serial session. Requires confirm=true."""
    _require_confirm(confirm, "write to a serial device")
    config = _config()
    session = sessions.get(session_id)
    _require_serial_write_target(session.port, config)
    payload = _decode(data, encoding)
    if append_newline and encoding != "hex":
        payload += b"\n"

    if len(payload) > config.max_write_bytes:
        raise ToolError(
            errors.WRITE_TOO_LARGE,
            f"Payload is {len(payload)} bytes; the limit is {config.max_write_bytes}.",
            "Raise [serial] max_write_bytes in the config, or send it in chunks.",
        )

    _write_budget(config).charge(session.port, len(payload))
    audit.require(
        "serial_write",
        "write to this serial device",
        port=session.port,
        bytes=len(payload),
        payload_sha256=audit.payload_digest(payload),
    )
    return ok(bytes_written=session.write(payload))


@mcp.tool(annotations=DESTRUCTIVE)
@guard
def serial_query(
    session_id: str,
    data: str,
    wait_ms: int = 1000,
    until: str | None = "\n",
    encoding: str = "utf8",
    confirm: bool = False,
) -> dict[str, Any]:
    """Write a command and read the reply in one call. Requires confirm=true."""
    _require_confirm(confirm, "write to a serial device")
    config = _config()
    session = sessions.get(session_id)
    _require_serial_write_target(session.port, config)
    payload = _decode(data, encoding)
    if encoding != "hex":
        payload += b"\n"

    if len(payload) > config.max_write_bytes:
        raise ToolError(errors.WRITE_TOO_LARGE, f"Payload is {len(payload)} bytes.")

    _write_budget(config).charge(session.port, len(payload))
    audit.require(
        "serial_query",
        "write to this serial device",
        port=session.port,
        bytes=len(payload),
        payload_sha256=audit.payload_digest(payload),
    )
    written, result = session.query(payload, min(wait_ms, MAX_WAIT_MS), until)

    return ok(
        bytes_written=written,
        data=_encode(result["data"], encoding),
        encoding=encoding,
        timed_out=result["timed_out"],
    )


@mcp.tool()
@guard
def serial_clear(session_id: str) -> dict[str, Any]:
    """Discard everything currently buffered for a session."""
    return ok(discarded=sessions.get(session_id).clear())


@mcp.tool()
@guard
def serial_close(session_id: str) -> dict[str, Any]:
    """Close a serial session and release the port."""
    port = sessions.get(session_id).port
    sessions.close(session_id)
    return ok(closed=port)


# --------------------------------------------------------------------------- flashing


@mcp.tool(annotations=READ_ONLY)
@guard
def list_fqbns(filter: str | None = None) -> dict[str, Any]:
    """List board identifiers (FQBNs) known to arduino-cli, optionally filtered."""
    return ok(boards=flash.list_fqbns(filter))


@mcp.tool()
@guard
def compile_sketch(sketch_dir: str, fqbn: str, port: str | None = None) -> dict[str, Any]:
    """Compile an Arduino sketch. On success returns a short-lived token required to upload."""
    config = _config()
    if not config.allow_flash:
        raise ToolError(errors.FLASH_DISABLED, "Flashing is disabled in your config.")
    serial = _usb_serial_for_compile(port, fqbn)
    return flash.compile_sketch(sketch_dir, fqbn, serial=serial, roots=config.sketch_roots)


@mcp.tool(annotations=DESTRUCTIVE)
@guard
def upload_sketch(
    sketch_dir: str,
    port: str,
    fqbn: str,
    upload_token: str,
    artifact_path: str = "",
    artifact_digest: str = "",
    confirm: bool = False,
) -> dict[str, Any]:
    """Flash a compiled sketch to a board.

    Requires both a token from a successful compile_sketch and confirm=True, because
    this overwrites the firmware on the device.
    """
    config = _config()
    if not config.allow_flash:
        raise ToolError(errors.FLASH_DISABLED, "Flashing is disabled in your config.")
    if not confirm:
        raise ToolError(
            errors.FLASH_UNCONFIRMED,
            "Refusing to flash without confirmation.",
            "This overwrites the board's firmware. Call again with confirm=true once the user agrees.",
        )

    resolved = policy.resolve_port(port)
    policy.check_readable(resolved)

    usb_serial = ""
    for board in enumerate_boards():
        if board["port"] != resolved:
            continue
        if board["board_type"] == "unknown":
            raise ToolError(
                errors.UNKNOWN_BOARD,
                f"{resolved} is an unrecognised device ({board['vid']}:{board['pid']}).",
                "Refusing to flash a board we cannot identify.",
            )
        if board["suggested_fqbn"] != fqbn:
            raise ToolError(
                errors.BOARD_MISMATCH,
                f"{resolved} is identified as {board['suggested_fqbn']}, not {fqbn}.",
                "Use the FQBN suggested for the connected board.",
            )
        usb_serial = (board.get("serial") or "").strip()
        break
    else:
        raise ToolError(errors.PORT_NOT_FOUND, f"{resolved} is not a connected development board.")

    # The port cannot be held open during an upload; reopen afterwards if it was.
    previous = sessions.by_port(resolved)
    baud = previous.baud if previous else None
    if previous is not None:
        sessions.close_port(resolved)

    restore_error = None
    restored = False
    try:
        result = flash.upload_sketch(
            sketch_dir,
            resolved,
            fqbn,
            upload_token,
            serial=usb_serial,
            artifact_path=artifact_path,
            artifact_digest=artifact_digest,
            roots=config.sketch_roots,
        )
    finally:
        if baud is not None:
            try:
                sessions.open(resolved, baud)
                restored = True
            except ToolError as exc:
                restore_error = exc

    if baud is None:
        return result
    if restored:
        return {**result, "session_restored": True}
    return {
        **result,
        "session_restored": False,
        "session_restore_error": {
            "code": restore_error.code if restore_error else "SERIAL_ERROR",
            "message": restore_error.message if restore_error else "Serial session could not be restored.",
            "hint": restore_error.hint if restore_error else "",
        },
    }


# --------------------------------------------------------------------------- gpio


@mcp.tool(annotations=READ_ONLY)
@guard
def pi_status(host: str | None = None) -> dict[str, Any]:
    """Check that a configured Raspberry Pi is reachable over SSH and report its model."""
    config = _config()
    return ok(**gpio_ssh.status(_resolve_host(host, config), config))


@mcp.tool(annotations=READ_ONLY)
@guard
def pi_inventory(host: str | None = None) -> dict[str, Any]:
    """Collect bounded, read-only Raspberry Pi identity, OS, kernel and health data."""
    config = _config()
    return ok(**gpio_ssh.inventory(_resolve_host(host, config), config))


@mcp.tool(annotations=READ_ONLY)
@guard
def jetson_status(host: str | None = None) -> dict[str, Any]:
    """Check that a configured Jetson is reachable over SSH and report its model."""
    config = _config()
    return ok(**jetson_ssh.status(_resolve_jetson_host(host, config), config))


@mcp.tool(annotations=READ_ONLY)
@guard
def jetson_inventory(host: str | None = None) -> dict[str, Any]:
    """Collect bounded, read-only Jetson identity, L4T, thermal and storage data. No GPIO."""
    config = _config()
    return ok(**jetson_ssh.inventory(_resolve_jetson_host(host, config), config))


@mcp.tool(annotations=READ_ONLY)
@guard
def gpio_list_pins(host: str | None = None) -> dict[str, Any]:
    """List every GPIO pin on the Pi with its current mode and level."""
    config = _config()
    return ok(pins=gpio_ssh.list_pins(_resolve_host(host, config), config))


@mcp.tool(annotations=READ_ONLY)
@guard
def gpio_read_pin(bcm: int, host: str | None = None) -> dict[str, Any]:
    """Read one GPIO pin's level and mode, by BCM number."""
    config = _config()
    target = _resolve_host(host, config)
    return ok(pin=gpio_ssh.read_pin(target, policy.check_pin(bcm, config), config))


@mcp.tool(annotations=DESTRUCTIVE)
@guard
def gpio_set_mode(bcm: int, mode: str, host: str | None = None, confirm: bool = False) -> dict[str, Any]:
    """Set a GPIO pin's mode: in, out, pull_up, pull_down or none. Requires confirm=true."""
    _require_confirm(confirm, "change GPIO mode")
    config = _config()
    target = _resolve_host(host, config)
    pin = policy.check_pin(bcm, config)
    _actuation_budget(config).charge(f"{target}:{pin}")
    audit.require("gpio_set_mode", "change this GPIO mode", host=target, bcm=pin, mode=mode)
    return ok(pin=gpio_ssh.set_mode(target, pin, mode, config))


@mcp.tool(annotations=DESTRUCTIVE)
@guard
def gpio_write_pin(bcm: int, level: int, host: str | None = None, confirm: bool = False) -> dict[str, Any]:
    """Drive a GPIO pin high (1) or low (0). Requires confirm=true."""
    _require_confirm(confirm, "drive a GPIO pin")
    config = _config()
    target = _resolve_host(host, config)
    pin = policy.check_pin(bcm, config)
    _actuation_budget(config).charge(f"{target}:{pin}")
    audit.require("gpio_write_pin", "drive this GPIO pin", host=target, bcm=pin, level=int(level))
    return ok(**gpio_ssh.write_pin(target, pin, int(level), config))


# --------------------------------------------------------------------------- weintek (OPC UA / MQTT)


@mcp.tool(annotations=READ_ONLY)
@guard
def weintek_opcua_read(endpoint: str, node: str) -> dict[str, Any]:
    """Read one allowlisted OPC UA node on a Weintek HMI.

    Not implemented. Every argument returns UNSUPPORTED_OPERATION, including a
    listed endpoint and node: refusing uniformly is what keeps allowlist
    membership from leaking to the model before there is anything to protect.

    When the client lands, call policy.check_weintek_opcua first and connect with
    the WeintekOpcUaTarget it returns -- it carries the validated security policy,
    mode and client certificate, so there is no way to reach an authorised
    endpoint without them.
    """
    raise support.unsupported("weintek_hmi", "opcua.read")


@mcp.tool(annotations=DESTRUCTIVE)
@guard
def weintek_opcua_write(endpoint: str, node: str, value: str, confirm: bool = False) -> dict[str, Any]:
    """Write one allowlisted OPC UA node on a Weintek HMI. Requires confirm=true.

    Not implemented; see weintek_opcua_read. A write must also pass through
    audit.require before the value leaves this machine.
    """
    _require_confirm(confirm, "write an OPC UA node")
    raise support.unsupported("weintek_hmi", "opcua.write")


@mcp.tool(annotations=DESTRUCTIVE)
@guard
def weintek_mqtt_publish(
    host: str,
    topic: str,
    payload: str,
    port: int = 1883,
    confirm: bool = False,
) -> dict[str, Any]:
    """Publish to one allowlisted MQTT topic on a Weintek HMI. Requires confirm=true.

    Not implemented. When it lands, call policy.check_weintek_mqtt and publish
    with the WeintekMqttTarget it returns: TLS is on by default and cleartext
    needs an explicit allow_insecure in the config. Audit before publishing.
    """
    _require_confirm(confirm, "publish MQTT")
    raise support.unsupported("weintek_hmi", "mqtt.publish")


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
