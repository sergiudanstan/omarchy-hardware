"""Structured tool errors.

Every tool returns a result dict rather than raising, so the model always gets a
machine-readable code plus a one-line remediation it can relay to the user.
"""

from __future__ import annotations

from typing import Any


class ToolError(Exception):
    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def as_result(self) -> dict[str, Any]:
        return {"ok": False, "error": {"code": self.code, "message": self.message, "hint": self.hint}}


def ok(**payload: Any) -> dict[str, Any]:
    return {"ok": True, **payload}


PERMISSION_DENIED_UUCP = "PERMISSION_DENIED_UUCP"
PORT_NOT_ALLOWED = "PORT_NOT_ALLOWED"
PORT_NOT_FOUND = "PORT_NOT_FOUND"
PORT_BUSY = "PORT_BUSY"
SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
BAUD_MISMATCH = "BAUD_MISMATCH"
SERIAL_ERROR = "SERIAL_ERROR"
UNKNOWN_ADAPTER = "UNKNOWN_ADAPTER"
SKETCH_NOT_ALLOWED = "SKETCH_NOT_ALLOWED"
WRITE_TOO_LARGE = "WRITE_TOO_LARGE"
RATE_LIMITED = "RATE_LIMITED"
TOOL_MISSING = "TOOL_MISSING"
HOST_NOT_ALLOWED = "HOST_NOT_ALLOWED"
PIN_NOT_ALLOWED = "PIN_NOT_ALLOWED"
SSH_FAILED = "SSH_FAILED"
FLASH_DISABLED = "FLASH_DISABLED"
FLASH_UNCONFIRMED = "FLASH_UNCONFIRMED"
UNCONFIRMED = "UNCONFIRMED"
# noqa S105: this is an error-code identifier returned to the model, not a secret.
INVALID_TOKEN = "INVALID_TOKEN"  # noqa: S105
UNKNOWN_BOARD = "UNKNOWN_BOARD"
BOARD_MISMATCH = "BOARD_MISMATCH"
AUDIT_LOG_FAILED = "AUDIT_LOG_FAILED"
ARTIFACT_INVALID = "ARTIFACT_INVALID"
CONFIG_ERROR = "CONFIG_ERROR"
UNSUPPORTED_OPERATION = "UNSUPPORTED_OPERATION"
INSECURE_TRANSPORT = "INSECURE_TRANSPORT"
INVALID_ARGUMENT = "INVALID_ARGUMENT"
SERVICE_UNREACHABLE = "SERVICE_UNREACHABLE"
SERVICE_ERROR = "SERVICE_ERROR"
