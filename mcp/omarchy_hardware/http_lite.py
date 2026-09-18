"""A narrow HTTP client for the MING stack's REST APIs.

Three properties matter more than features here:

- No redirects. A 3xx is an error, so an allowlisted base URL cannot bounce the
  request -- and its bearer token -- to a host nobody configured.
- No proxies. HTTP(S)_PROXY from Claude Code's environment is ignored, so the
  token only ever travels to the configured host.
- Bounded responses. Reads stop at a byte cap instead of buffering whatever the
  server chooses to send.
"""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

MAX_RESPONSE_BYTES = 512 * 1024
MAX_ERROR_DETAIL = 200


class HttpError(Exception):
    """A transport failure or a non-2xx answer. `status` is None when nothing was received."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001 - urllib signature
        return None


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes

    def json(self) -> Any:
        try:
            return json.loads(self.body)
        except ValueError as exc:
            raise HttpError("the service answered with something that is not JSON", self.status) from exc


def _opener(url: str, ca_file: str | None) -> urllib.request.OpenerDirector:
    handlers: list[urllib.request.BaseHandler] = [urllib.request.ProxyHandler({}), _NoRedirect()]
    if url.startswith("https://"):
        try:
            context = ssl.create_default_context(cafile=os.path.expanduser(ca_file) if ca_file else None)
        except (OSError, ssl.SSLError) as exc:
            raise HttpError(f"cannot load the configured ca_file ({type(exc).__name__})") from exc
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        handlers.append(urllib.request.HTTPSHandler(context=context))
    return urllib.request.build_opener(*handlers)


def request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: float,
    ca_file: str | None = None,
    max_bytes: int = MAX_RESPONSE_BYTES,
) -> Response:
    """Send one request to an absolute http(s) URL the caller built from configuration."""
    if not url.startswith(("http://", "https://")):
        raise HttpError("refusing a non-HTTP URL")
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})  # noqa: S310 - scheme checked above
    try:
        with _opener(url, ca_file).open(req, timeout=timeout) as handle:
            data = handle.read(max_bytes + 1)
            status = handle.status
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise HttpError(f"the service redirected ({exc.code}); redirects are not followed", exc.code) from None
        raise HttpError(f"the service answered HTTP {exc.code}{_detail(exc)}", exc.code) from None
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, ssl.SSLError):
            raise HttpError(f"TLS failed ({reason.reason or type(reason).__name__})") from None
        raise HttpError(f"could not reach the service ({type(reason).__name__})") from None
    if len(data) > max_bytes:
        raise HttpError(f"the response exceeded {max_bytes} bytes; narrow the request", status)
    return Response(status=status, body=data)


def _detail(exc: urllib.error.HTTPError) -> str:
    """A short, single-line excerpt of an error body; services put the useful reason there."""
    try:
        raw = exc.read(MAX_ERROR_DETAIL * 4)
    except OSError:
        return ""
    text = raw.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None  # a plain-text or HTML error page: use it as it is
    if isinstance(parsed, dict):
        text = str(parsed.get("message") or parsed.get("error") or text)
    text = " ".join(text.split())[:MAX_ERROR_DETAIL]
    return f": {text}" if text else ""
