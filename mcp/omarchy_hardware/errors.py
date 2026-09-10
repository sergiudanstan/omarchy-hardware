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
SERIAL_ERROR = "SERIAL_ERROR"
WRITE_TOO_LARGE = "WRITE_TOO_LARGE"
RATE_LIMITED = "RATE_LIMITED"
TOOL_MISSING = "TOOL_MISSING"
HOST_NOT_ALLOWED = "HOST_NOT_ALLOWED"
PIN_NOT_ALLOWED = "PIN_NOT_ALLOWED"
SSH_FAILED = "SSH_FAILED"
FLASH_DISABLED = "FLASH_DISABLED"
FLASH_UNCONFIRMED = "FLASH_UNCONFIRMED"
# noqa S105: this is an error-code identifier returned to the model, not a secret.
INVALID_TOKEN = "INVALID_TOKEN"  # noqa: S105
UNKNOWN_BOARD = "UNKNOWN_BOARD"
CONFIG_ERROR = "CONFIG_ERROR"
