"""MCP server exposing dev-board hardware to Claude.

Every device path passes through policy.resolve_port; every Pi host and pin passes
through the config allowlists. Tools return structured results instead of raising, so
the model always receives an actionable error code.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from . import __version__, errors, flash, gpio_ssh, policy, support
from .boards import enumerate_boards
from .config import Config, ConfigError
from .config import load as load_config
from .errors import ToolError, ok
from .serial_session import MAX_WAIT_MS, SessionManager

mcp = MCPServer(name="omarchy-hardware", version=__version__)

READ_ONLY = ToolAnnotations(read_only_hint=True)
DESTRUCTIVE = ToolAnnotations(destructive_hint=True)
sessions = SessionManager()
_budget: policy.WriteBudget | None = None


def _config() -> Config:
    try:
        return load_config()
    except ConfigError as exc:
        raise ToolError(errors.CONFIG_ERROR, str(exc)) from exc


def _write_budget(config: Config) -> policy.WriteBudget:
    global _budget
    if _budget is None:
        _budget = policy.WriteBudget(config.write_budget_bytes_per_min)
    return _budget


def guard(fn: Callable) -> Callable:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return fn(*args, **kwargs)
        except ToolError as exc:
            return exc.as_result()
        except Exception as exc:  # never let a traceback escape as a protocol error
            return ToolError(type(exc).__name__, str(exc)).as_result()

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
            f"Configured: {', '.join(config.pi_hosts)}.",
        )
    return policy.check_host(host, config)


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
    """Open a serial session. Idempotent: reopening a port returns the existing session."""
    resolved = policy.resolve_port(port)
    policy.check_readable(resolved)
    session = sessions.open(resolved, int(baud))
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
) -> dict[str, Any]:
    """Write data to an open serial session."""
    config = _config()
    session = sessions.get(session_id)
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
    return ok(bytes_written=session.write(payload))


@mcp.tool(annotations=DESTRUCTIVE)
@guard
def serial_query(
    session_id: str,
    data: str,
    wait_ms: int = 1000,
    until: str | None = "\n",
    encoding: str = "utf8",
) -> dict[str, Any]:
    """Write a command and read the reply in one call. The common case for talking to a board."""
    config = _config()
    session = sessions.get(session_id)
    payload = _decode(data, encoding)
    if encoding != "hex":
        payload += b"\n"

    if len(payload) > config.max_write_bytes:
        raise ToolError(errors.WRITE_TOO_LARGE, f"Payload is {len(payload)} bytes.")

    _write_budget(config).charge(session.port, len(payload))
    session.clear()
    written = session.write(payload)
    result = session.read(4096, min(wait_ms, MAX_WAIT_MS), until)

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
def compile_sketch(sketch_dir: str, fqbn: str) -> dict[str, Any]:
    """Compile an Arduino sketch. On success returns a short-lived token required to upload."""
    if not _config().allow_flash:
        raise ToolError(errors.FLASH_DISABLED, "Flashing is disabled in your config.")
    return flash.compile_sketch(sketch_dir, fqbn)


@mcp.tool(annotations=DESTRUCTIVE)
@guard
def upload_sketch(
    sketch_dir: str,
    port: str,
    fqbn: str,
    upload_token: str,
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
        result = flash.upload_sketch(sketch_dir, resolved, fqbn, upload_token)
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
def gpio_set_mode(bcm: int, mode: str, host: str | None = None) -> dict[str, Any]:
    """Set a GPIO pin's mode: in, out, pull_up, pull_down or none."""
    config = _config()
    target = _resolve_host(host, config)
    return ok(pin=gpio_ssh.set_mode(target, policy.check_pin(bcm, config), mode, config))


@mcp.tool(annotations=DESTRUCTIVE)
@guard
def gpio_write_pin(bcm: int, level: int, host: str | None = None) -> dict[str, Any]:
    """Drive a GPIO pin high (1) or low (0)."""
    config = _config()
    target = _resolve_host(host, config)
    return ok(**gpio_ssh.write_pin(target, policy.check_pin(bcm, config), int(level), config))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
