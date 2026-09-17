"""Upload-token tests.

Flashing overwrites firmware, so the token is the gate that stops an LLM reaching
it in one unconsidered call. These lock in that the token is bound to the exact
sketch, board and expiry it was minted for.
"""

import json
import time

import pytest

from omarchy_hardware import audit, flash
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
    monkeypatch.setattr(audit, "STATE_DIR", state_dir)

    flash._prepare_upload_log({"sketch_dir": SKETCH, "port": "/dev/ttyACM0", "fqbn": FQBN})

    assert state_dir.stat().st_mode & 0o777 == 0o700
    assert (state_dir / audit.LOG_NAME).stat().st_mode & 0o777 == 0o600
    assert '"event":"upload_started"' in (state_dir / audit.LOG_NAME).read_text(encoding="utf-8")


def test_upload_is_blocked_when_audit_log_cannot_be_written(monkeypatch, tmp_path):
    state_dir = tmp_path / "state"
    monkeypatch.setattr(audit, "STATE_DIR", state_dir)
    monkeypatch.setattr(audit, "record", lambda *_a, **_k: (_ for _ in ()).throw(OSError("denied")))

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


def _make_artifact(tmp_path, name="build", payload=b"firmware"):
    artifact = tmp_path / name
    artifact.mkdir()
    (artifact / "sketch.hex").write_bytes(payload)
    return artifact


def test_token_is_bound_to_artifact_path_and_digest(tmp_path):
    artifact = _make_artifact(tmp_path)
    digest = flash._artifact_digest(str(artifact))
    token = flash.mint_token(SKETCH, FQBN, "ABC", str(artifact.resolve()), digest)

    flash.verify_token(token, SKETCH, FQBN, "ABC", str(artifact.resolve()), digest)

    with pytest.raises(ToolError) as excinfo:
        flash.verify_token(token, SKETCH, FQBN, "ABC", str(artifact.resolve()), "0" * 64)
    assert excinfo.value.code == "INVALID_TOKEN"

    other = _make_artifact(tmp_path, "other", b"other")
    with pytest.raises(ToolError) as excinfo:
        flash.verify_token(token, SKETCH, FQBN, "ABC", str(other.resolve()), digest)
    assert excinfo.value.code == "INVALID_TOKEN"


def test_upload_rejects_replaced_build_artifact(monkeypatch, tmp_path):
    root = tmp_path / "Arduino"
    sketch = root / "blink"
    sketch.mkdir(parents=True)
    artifact = _make_artifact(tmp_path)
    digest = flash._artifact_digest(str(artifact))
    token = flash.mint_token(str(sketch.resolve()), FQBN, "ABC", str(artifact.resolve()), digest)

    (artifact / "sketch.hex").write_bytes(b"tampered-firmware")

    with pytest.raises(ToolError) as excinfo:
        flash.upload_sketch(
            str(sketch),
            "/dev/ttyACM0",
            FQBN,
            token,
            serial="ABC",
            artifact_path=str(artifact),
            artifact_digest=digest,
            roots=(str(root),),
        )
    assert excinfo.value.code == "ARTIFACT_INVALID"


def test_upload_rejects_missing_artifact_binding(tmp_path):
    root = tmp_path / "Arduino"
    sketch = root / "blink"
    sketch.mkdir(parents=True)
    token = flash.mint_token(str(sketch.resolve()), FQBN)

    with pytest.raises(ToolError) as excinfo:
        flash.upload_sketch(
            str(sketch),
            "/dev/ttyACM0",
            FQBN,
            token,
            roots=(str(root),),
        )
    assert excinfo.value.code == "ARTIFACT_INVALID"


def test_artifact_digest_rejects_symlinks(tmp_path):
    artifact = _make_artifact(tmp_path)
    target = tmp_path / "secret.bin"
    target.write_bytes(b"secret")
    (artifact / "link.hex").symlink_to(target)

    with pytest.raises(ToolError) as excinfo:
        flash._artifact_digest(str(artifact))
    assert excinfo.value.code == "ARTIFACT_INVALID"


def test_compile_sketch_mints_artifact_bound_token(monkeypatch, tmp_path):
    root = tmp_path / "Arduino"
    sketch = root / "blink"
    sketch.mkdir(parents=True)
    artifact = _make_artifact(tmp_path)

    class Result:
        returncode = 0
        stdout = (
            '{"builder_result":{"build_path":'
            + json.dumps(str(artifact))
            + "}}"
        )
        stderr = ""

    monkeypatch.setattr(flash, "_arduino_cli", lambda *_args, **_kwargs: Result())

    result = flash.compile_sketch(str(sketch), FQBN, serial="ABC", roots=(str(root),))

    assert result["ok"] is True
    assert result["artifact_path"] == str(artifact.resolve())
    assert result["artifact_digest"] == flash._artifact_digest(str(artifact))
    flash.verify_token(
        result["upload_token"],
        result["sketch_dir"],
        FQBN,
        "ABC",
        result["artifact_path"],
        result["artifact_digest"],
    )


def test_compile_sketch_refuses_missing_build_path(monkeypatch, tmp_path):
    root = tmp_path / "Arduino"
    sketch = root / "blink"
    sketch.mkdir(parents=True)

    class Result:
        returncode = 0
        stdout = '{"builder_result":{}}'
        stderr = ""

    monkeypatch.setattr(flash, "_arduino_cli", lambda *_args, **_kwargs: Result())

    with pytest.raises(ToolError) as excinfo:
        flash.compile_sketch(str(sketch), FQBN, roots=(str(root),))
    assert excinfo.value.code == "ARTIFACT_INVALID"


def test_upload_uses_verified_snapshot_and_cleans_it(monkeypatch, tmp_path):
    import subprocess
    from pathlib import Path

    sketch = tmp_path / "blink"
    sketch.mkdir()
    artifact = _make_artifact(tmp_path)
    digest = flash._artifact_digest(str(artifact))
    token = flash.mint_token(str(sketch), FQBN, "", str(artifact), digest)
    uploads = []

    def change_original(_record):
        (artifact / "sketch.hex").write_bytes(b"concurrent rebuild")

    def upload(args):
        snapshot = Path(args[args.index("--input-dir") + 1])
        uploads.append(snapshot)
        assert snapshot != artifact
        assert snapshot.parent.stat().st_mode & 0o777 == 0o700
        assert (snapshot / "sketch.hex").read_bytes() == b"firmware"
        return subprocess.CompletedProcess(args, 0, "uploaded", "")

    monkeypatch.setattr(flash, "_prepare_upload_log", change_original)
    monkeypatch.setattr(audit, "note", lambda *_a, **_k: None)
    monkeypatch.setattr(flash, "_arduino_cli", upload)
    result = flash.upload_sketch(str(sketch), "/dev/ttyACM0", FQBN, token,
                                artifact_path=str(artifact), artifact_digest=digest, roots=(str(tmp_path),))
    assert result["ok"] is True
    assert len(uploads) == 1
    assert not uploads[0].parent.exists()


def test_snapshot_rejects_mutation_during_copy(monkeypatch, tmp_path):
    artifact = _make_artifact(tmp_path)
    digest = flash._artifact_digest(str(artifact))
    copy = flash.shutil.copytree

    def changed_copy(source, destination, **kwargs):
        (artifact / "sketch.hex").write_bytes(b"changed during copy")
        return copy(source, destination, **kwargs)

    monkeypatch.setattr(flash.shutil, "copytree", changed_copy)
    with pytest.raises(ToolError, match="changed since compilation"):
        with flash._artifact_snapshot(str(artifact), digest):
            pytest.fail("Changed snapshot must not reach the uploader")


def test_snapshot_rejects_directory_symlinks(tmp_path):
    artifact = _make_artifact(tmp_path)
    target = tmp_path / "outside"
    target.mkdir()
    (artifact / "linked").symlink_to(target, target_is_directory=True)
    with pytest.raises(ToolError) as error:
        with flash._artifact_snapshot(str(artifact), "0" * 64):
            pytest.fail("Symlink must be rejected")
    assert error.value.code == "ARTIFACT_INVALID"


def test_snapshot_is_removed_when_upload_raises(tmp_path):
    from pathlib import Path

    artifact = _make_artifact(tmp_path)
    snapshot = None
    try:
        with flash._artifact_snapshot(str(artifact), flash._artifact_digest(str(artifact))) as snapshot:
            assert Path(snapshot).exists()
            raise RuntimeError("upload failed")
    except RuntimeError as exc:
        assert str(exc) == "upload failed"
    assert snapshot is not None
    assert not Path(snapshot).parent.exists()


@pytest.mark.parametrize(
    "fqbn",
    [
        "--config-file=/tmp/evil.yaml",
        "-v",
        "arduino:avr",
        "arduino avr uno",
        "arduino:avr:uno;rm -rf /",
        "arduino:avr:uno\n--verbose",
        "",
    ],
)
def test_fqbn_must_look_like_an_fqbn(fqbn):
    """Never rely on arduino-cli's parser to refuse a value that starts with '-'."""
    with pytest.raises(ToolError) as excinfo:
        flash.check_fqbn(fqbn)
    assert excinfo.value.code == "UNKNOWN_BOARD"


@pytest.mark.parametrize(
    "fqbn",
    [
        "arduino:avr:uno",
        "arduino:renesas_uno:unor4wifi",
        "arduino:avr:nano:cpu=atmega328old",
        "STMicroelectronics:stm32:Nucleo_64:pnum=NUCLEO_F401RE",
    ],
)
def test_real_fqbns_are_accepted(fqbn):
    assert flash.check_fqbn(fqbn) == fqbn
