"""Line matchers for serial_expect.

There is deliberately no regular-expression mode. Python's `re` cannot be
interrupted and holds the GIL, so one backtracking pattern chosen by the model,
applied to a line chosen by the device, would freeze the whole MCP server. The
modes here are linear in the line length:

- literal: the line contains the text
- prefix:  the line starts with the text
- json:    the line is a JSON object containing every key/value in the text
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from . import errors
from .errors import ToolError

MODES = ("literal", "prefix", "json")
MAX_MATCH_CHARS = 200
MAX_JSON_LINE = 4096


def _bad(message: str) -> ToolError:
    return ToolError(errors.INVALID_ARGUMENT, message, f"mode is one of {', '.join(MODES)}; match is at most "
                     f"{MAX_MATCH_CHARS} characters.")


def build(mode: str, match: str) -> Callable[[str], Any]:
    if mode not in MODES:
        raise _bad(f"Unknown mode {mode!r}.")
    if len(match) > MAX_MATCH_CHARS:
        raise _bad("match is too long.")

    if mode == "literal":
        if not match:
            raise _bad("literal mode needs a non-empty match.")
        return lambda line: match in line

    if mode == "prefix":
        if not match:
            raise _bad("prefix mode needs a non-empty match.")
        return lambda line: line.startswith(match)

    expected: dict[str, Any] = {}
    if match.strip():
        try:
            expected = json.loads(match)
        except json.JSONDecodeError as exc:
            raise _bad(f"match is not valid JSON: {exc.msg}.") from exc
        if not isinstance(expected, dict):
            raise _bad("In json mode, match must be a JSON object such as {\"status\": \"ok\"}.")

    def json_line(line: str) -> dict[str, Any] | None:
        text = line.strip()
        if not text.startswith("{") or len(text) > MAX_JSON_LINE:
            return None
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, RecursionError):
            return None
        if not isinstance(parsed, dict):
            return None
        # Types must agree too: in Python True == 1, but a device sending 1 did not say true.
        if all(key in parsed and type(parsed[key]) is type(value) and parsed[key] == value
               for key, value in expected.items()):
            # Wrapped so an empty object still counts as a match.
            return {"json": parsed}
        return None

    return json_line
