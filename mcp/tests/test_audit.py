import json

import pytest

from omarchy_hardware import audit
from omarchy_hardware.errors import ToolError


@pytest.fixture(autouse=True)
def state_dir(monkeypatch, tmp_path):
    directory = tmp_path / "state"
    monkeypatch.setattr(audit, "STATE_DIR", directory)
    monkeypatch.setattr(audit, "_head", None)
    monkeypatch.setattr(audit, "_head_path", None)
    return directory


def test_log_is_created_private_and_chained(state_dir):
    audit.record("gpio_write_pin", host="pi.local", bcm=17, level=1)
    audit.record("gpio_write_pin", host="pi.local", bcm=17, level=0)

    assert state_dir.stat().st_mode & 0o777 == 0o700
    assert audit.log_path().stat().st_mode & 0o777 == 0o600

    lines = [json.loads(line) for line in audit.log_path().read_text(encoding="utf-8").splitlines()]
    assert [entry["level"] for entry in lines] == [1, 0]
    assert lines[0]["prev"] == audit.GENESIS
    assert lines[1]["prev"] == lines[0]["hash"]
    assert audit.verify() == {"ok": True, "records": 2, "path": str(audit.log_path())}


def test_an_edited_record_breaks_the_chain(state_dir):
    audit.record("gpio_write_pin", host="pi.local", bcm=17, level=1)
    audit.record("gpio_write_pin", host="pi.local", bcm=17, level=0)

    lines = audit.log_path().read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[0])
    tampered["level"] = 0  # the operator claims the relay was never energised
    lines[0] = json.dumps(tampered, separators=(",", ":"), sort_keys=True)
    audit.log_path().write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = audit.verify()
    assert result["ok"] is False
    assert result["broken_at_line"] == 1


def test_a_deleted_record_breaks_the_chain(state_dir):
    for level in (1, 0, 1):
        audit.record("gpio_write_pin", host="pi.local", bcm=17, level=level)

    lines = audit.log_path().read_text(encoding="utf-8").splitlines()
    del lines[1]
    audit.log_path().write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = audit.verify()
    assert result["ok"] is False
    assert result["broken_at_line"] == 2


def test_chain_survives_a_restart(state_dir, monkeypatch):
    audit.record("serial_write", port="/dev/ttyACM0", bytes=4)

    # A new process starts with no in-memory head and must pick the chain back up
    # from disk rather than starting a second, unconnected chain.
    monkeypatch.setattr(audit, "_head", None)
    monkeypatch.setattr(audit, "_head_path", None)
    audit.record("serial_write", port="/dev/ttyACM0", bytes=8)

    assert audit.verify()["records"] == 2


def test_verify_reports_an_empty_log_as_intact(state_dir):
    assert audit.verify() == {"ok": True, "records": 0, "path": str(audit.log_path())}


def test_require_refuses_the_operation_when_the_log_is_unwritable(monkeypatch):
    monkeypatch.setattr(audit, "record", lambda *_a, **_k: (_ for _ in ()).throw(OSError("read-only")))

    with pytest.raises(ToolError) as excinfo:
        audit.require("gpio_write_pin", "drive this GPIO pin", host="pi.local", bcm=17)

    assert excinfo.value.code == "AUDIT_LOG_FAILED"
    assert "drive this GPIO pin" in excinfo.value.message


def test_payloads_are_recorded_by_digest_not_content(state_dir):
    audit.record(
        "serial_write",
        port="/dev/ttyACM0",
        bytes=11,
        payload_sha256=audit.payload_digest(b"SET PUMP ON"),
    )

    written = audit.log_path().read_text(encoding="utf-8")
    assert "SET PUMP ON" not in written
    assert audit.payload_digest(b"SET PUMP ON") in written
