"""Upload-token tests.

Flashing overwrites firmware, so the token is the gate that stops an LLM reaching
it in one unconsidered call. These lock in that the token is bound to the exact
sketch, board and expiry it was minted for.
"""

import time

import pytest

from omarchy_hardware import flash
from omarchy_hardware.errors import ToolError

SKETCH = "/sketches/blink"
FQBN = "arduino:avr:uno"


@pytest.fixture
def token():
    return flash.mint_token(SKETCH, FQBN)


def test_genuine_token_verifies(token):
    flash.verify_token(token, SKETCH, FQBN)


def test_token_is_bound_to_the_sketch(token):
    with pytest.raises(ToolError) as excinfo:
        flash.verify_token(token, "/sketches/other", FQBN)
    assert excinfo.value.code == "INVALID_TOKEN"


def test_token_is_bound_to_the_board(token):
    with pytest.raises(ToolError) as excinfo:
        flash.verify_token(token, SKETCH, "arduino:avr:mega")
    assert excinfo.value.code == "INVALID_TOKEN"


def test_token_is_bound_to_usb_serial():
    token = flash.mint_token(SKETCH, FQBN, "ABC123")
    flash.verify_token(token, SKETCH, FQBN, "ABC123")
    with pytest.raises(ToolError) as excinfo:
        flash.verify_token(token, SKETCH, FQBN, "OTHER")
    assert excinfo.value.code == "INVALID_TOKEN"
    with pytest.raises(ToolError):
        flash.verify_token(token, SKETCH, FQBN, "")


def test_token_fields_cannot_collide_via_pipe():
    left = flash.mint_token("X", "Y|Z", "")
    with pytest.raises(ToolError):
        flash.verify_token(left, "X|Y", "Z", "")


def test_tampered_signature_is_rejected(token):
    signature, expiry = token.rsplit(".", 1)
    forged = ("B" if signature[0] != "B" else "C") + signature[1:] + "." + expiry
    with pytest.raises(ToolError):
        flash.verify_token(forged, SKETCH, FQBN)


def test_expiry_cannot_be_extended(token):
    """The expiry is inside the HMAC, so pushing it out invalidates the signature."""
    signature, expiry = token.rsplit(".", 1)
    with pytest.raises(ToolError):
        flash.verify_token(f"{signature}.{int(expiry) + 99999}", SKETCH, FQBN)


def test_expired_token_is_rejected(monkeypatch):
    token = flash.mint_token(SKETCH, FQBN)
    expiry = int(token.rsplit(".", 1)[1])
    monkeypatch.setattr(time, "time", lambda: expiry + 1)
    with pytest.raises(ToolError) as excinfo:
        flash.verify_token(token, SKETCH, FQBN)
    assert excinfo.value.code == "INVALID_TOKEN"


@pytest.mark.parametrize("bad", ["", "nonsense", "no-dot-separator", "a.b", "."])
def test_malformed_tokens_are_rejected(bad):
    with pytest.raises(ToolError):
        flash.verify_token(bad, SKETCH, FQBN)


def test_tokens_differ_between_sketches():
    assert flash.mint_token("/sketches/one", FQBN) != flash.mint_token("/sketches/two", FQBN)


def test_upload_log_is_created_with_restrictive_permissions(monkeypatch, tmp_path):
    state_dir = tmp_path / "state"
    monkeypatch.setattr(flash, "STATE_DIR", state_dir)

    flash._prepare_upload_log({"sketch_dir": SKETCH, "port": "/dev/ttyACM0", "fqbn": FQBN})

    assert state_dir.stat().st_mode & 0o777 == 0o700
    assert (state_dir / "flash.log").stat().st_mode & 0o777 == 0o600
    assert '"event": "upload_started"' in (state_dir / "flash.log").read_text(encoding="utf-8")


def test_upload_is_blocked_when_audit_log_cannot_be_written(monkeypatch, tmp_path):
    state_dir = tmp_path / "state"
    monkeypatch.setattr(flash, "STATE_DIR", state_dir)
    monkeypatch.setattr(flash, "_append_upload_log", lambda _record: (_ for _ in ()).throw(OSError("denied")))

    with pytest.raises(ToolError) as excinfo:
        flash._prepare_upload_log({"sketch_dir": SKETCH, "port": "/dev/ttyACM0", "fqbn": FQBN})

    assert excinfo.value.code == "AUDIT_LOG_FAILED"


def test_sketch_dir_must_be_under_configured_roots(tmp_path):
    root = tmp_path / "Arduino"
    other = tmp_path / "other"
    root.mkdir()
    other.mkdir()
    (root / "blink").mkdir()

    assert flash.resolve_sketch_dir(str(root / "blink"), (str(root),)).endswith("blink")
    with pytest.raises(ToolError) as excinfo:
        flash.resolve_sketch_dir(str(other), (str(root),))
    assert excinfo.value.code == "SKETCH_NOT_ALLOWED"
    with pytest.raises(ToolError) as excinfo:
        flash.resolve_sketch_dir(str(root / "blink"), ())
    assert excinfo.value.code == "SKETCH_NOT_ALLOWED"
