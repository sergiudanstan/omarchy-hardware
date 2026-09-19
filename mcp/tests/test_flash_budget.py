import subprocess
import time

import pytest

from omarchy_hardware import config as config_module
from omarchy_hardware import errors, policy, server
from omarchy_hardware.config import Config

BOARD = {"port": "/dev/ttyACM0", "vid": "2341", "pid": "0043", "serial": "A1", "board_type": "arduino_uno",
         "suggested_fqbn": "arduino:avr:uno"}
ESP32 = {"port": "/dev/ttyUSB0", "vid": "10c4", "pid": "ea60", "serial": "0001", "board_type": "esp32",
         "suggested_fqbn": "esp32:esp32:esp32", "busy": False}


def test_budget_is_hourly_and_per_target(monkeypatch):
    budget = policy.FlashBudget(2)
    budget.charge("a")
    budget.charge("a")
    budget.charge("b")
    with pytest.raises(errors.ToolError) as caught:
        budget.charge("a")
    assert caught.value.code == errors.RATE_LIMITED
    assert "2/2 uploads in the last hour" in caught.value.message

    now = time.monotonic()
    monkeypatch.setattr(policy.time, "monotonic", lambda: now + 1800)
    with pytest.raises(errors.ToolError):
        budget.charge("a")
    monkeypatch.setattr(policy.time, "monotonic", lambda: now + 3601)
    budget.charge("a")


def test_other_budgets_keep_their_one_minute_window():
    assert policy.WriteBudget(1).window == 60.0
    assert policy.ActuationBudget(1).window_text == "the last minute"


def test_upload_is_refused_once_the_board_budget_is_spent(monkeypatch):
    config = Config(allow_flash=True, sketch_roots=("/s",), max_uploads_per_hour=2)
    monkeypatch.setattr(server, "_config", lambda: config)
    monkeypatch.setattr(server, "_flash_budget", None)
    monkeypatch.setattr(server.policy, "resolve_port", lambda port: port)
    monkeypatch.setattr(server.policy, "check_readable", lambda port: None)
    monkeypatch.setattr(server, "enumerate_boards", lambda: [BOARD])
    monkeypatch.setattr(server.sessions, "by_port", lambda port: None)
    calls = []

    def fake_upload(*args, before_write=None, **kwargs):
        before_write()
        calls.append(1)
        return {"ok": False}

    monkeypatch.setattr(server.flash, "upload_sketch", fake_upload)

    for _ in range(2):
        server.upload_sketch("/s/x", "/dev/ttyACM0", "arduino:avr:uno", "t", confirm=True)
    third = server.upload_sketch("/s/x", "/dev/ttyACM0", "arduino:avr:uno", "t", confirm=True)
    assert third["error"]["code"] == errors.RATE_LIMITED
    assert len(calls) == 2, "the refused upload never reached arduino-cli"

    # Another board is not held back by the first one.
    other = {**BOARD, "port": "/dev/ttyACM1", "serial": "B2"}
    monkeypatch.setattr(server, "enumerate_boards", lambda: [other])
    server.upload_sketch("/s/x", "/dev/ttyACM1", "arduino:avr:uno", "t", confirm=True)
    assert len(calls) == 3


@pytest.mark.parametrize("value", ["0", "1001", "true", '"ten"'])
def test_config_bounds(monkeypatch, tmp_path, value):
    path = tmp_path / "config.toml"
    path.write_text(f"[flash]\nmax_uploads_per_hour = {value}\n")
    path.chmod(0o600)
    monkeypatch.setattr(config_module, "CONFIG_PATH", path)
    with pytest.raises(config_module.ConfigError, match="max_uploads_per_hour"):
        config_module.load()


def test_config_default():
    assert Config().max_uploads_per_hour == 30


def test_uploads_refused_before_the_programmer_do_not_spend_the_budget(monkeypatch, tmp_path):
    from omarchy_hardware import audit

    sketch = tmp_path / "blink"
    sketch.mkdir()
    artifact = tmp_path / "build"
    artifact.mkdir()
    (artifact / "blink.ino.hex").write_bytes(b"firmware")
    digest = server.flash._artifact_digest(str(artifact))
    config = Config(allow_flash=True, sketch_roots=(str(tmp_path),), max_uploads_per_hour=1)
    monkeypatch.setattr(audit, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(server, "_config", lambda: config)
    monkeypatch.setattr(server, "_flash_budget", None)
    monkeypatch.setattr(server.policy, "resolve_port", lambda port: port)
    monkeypatch.setattr(server.policy, "check_readable", lambda port: None)
    monkeypatch.setattr(server, "enumerate_boards", lambda: [BOARD])
    monkeypatch.setattr(server.sessions, "by_port", lambda port: None)
    ran = []

    def cli(args, **kwargs):
        ran.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(server.flash, "_arduino_cli", cli)

    for _ in range(3):
        bad = server.upload_sketch(str(sketch), "/dev/ttyACM0", "arduino:avr:uno", "forged.123",
                                   artifact_path=str(artifact), artifact_digest=digest, confirm=True)
        assert bad["error"]["code"] == errors.INVALID_TOKEN
    assert ran == []

    token = server.flash.mint_token(str(sketch), "arduino:avr:uno", "A1", str(artifact), digest)
    good = server.upload_sketch(str(sketch), "/dev/ttyACM0", "arduino:avr:uno", token,
                                artifact_path=str(artifact), artifact_digest=digest, confirm=True)
    assert good["ok"] is True, good
    again = server.upload_sketch(str(sketch), "/dev/ttyACM0", "arduino:avr:uno", token,
                                 artifact_path=str(artifact), artifact_digest=digest, confirm=True)
    assert again["error"]["code"] == errors.RATE_LIMITED


def _patch_esp_flash(monkeypatch, config):
    monkeypatch.setattr(server, "_config", lambda: config)
    monkeypatch.setattr(server, "_flash_budget", None)
    monkeypatch.setattr(server.policy, "resolve_port", lambda port: port)
    monkeypatch.setattr(server.policy, "check_readable", lambda port: None)
    monkeypatch.setattr(server, "enumerate_boards", lambda: [ESP32])
    monkeypatch.setattr(server.sessions, "by_port", lambda port: None)

    def fake_upload(*args, before_write=None, **kwargs):
        before_write()
        return {"ok": False}

    monkeypatch.setattr(server.flash, "upload_sketch", fake_upload)


def test_esp32_upload_and_restore_share_the_chip_budget(monkeypatch, tmp_path):
    _patch_esp_flash(monkeypatch, Config(allow_flash=True, sketch_roots=("/s",), max_uploads_per_hour=1))
    monkeypatch.setattr(server.backup, "identify", lambda port: {"mac": "24:0a:c4:aa:bb:cc", "chip": "ESP32"})
    server.upload_sketch("/s/x", ESP32["port"], ESP32["suggested_fqbn"], "t", confirm=True)

    meta = {"backup_id": "20260919-120000-aaaaaaaaaaaa", "mac": "24:0a:c4:aa:bb:cc", "sha256": "ab", "bytes": 1}
    monkeypatch.setattr(server.backup, "load", lambda backup_id: (meta, tmp_path / "image.bin"))

    def fake_restore(port, loaded, image, before_write=None):
        before_write()
        return {"backup_id": loaded["backup_id"], "mac": loaded["mac"], "bytes": 1}

    monkeypatch.setattr(server.backup, "restore", fake_restore)
    refused = server.firmware_restore(ESP32["port"], meta["backup_id"], confirm=True)
    assert refused["error"]["code"] == errors.RATE_LIMITED


def test_esp32_uploads_are_keyed_by_chip_mac_not_usb_serial(monkeypatch):
    _patch_esp_flash(monkeypatch, Config(allow_flash=True, sketch_roots=("/s",), max_uploads_per_hour=1))
    macs = iter(["24:0a:c4:aa:bb:cc", "24:0a:c4:00:00:01", "24:0a:c4:aa:bb:cc"])
    monkeypatch.setattr(server.backup, "identify", lambda port: {"mac": next(macs), "chip": "ESP32"})

    first = server.upload_sketch("/s/x", ESP32["port"], ESP32["suggested_fqbn"], "t", confirm=True)
    second = server.upload_sketch("/s/x", ESP32["port"], ESP32["suggested_fqbn"], "t", confirm=True)
    assert first["ok"] is False and second["ok"] is False
    assert "error" not in first and "error" not in second, (first, second)

    same_chip = server.upload_sketch("/s/x", ESP32["port"], ESP32["suggested_fqbn"], "t", confirm=True)
    assert same_chip["error"]["code"] == errors.RATE_LIMITED


def test_esp32_rate_limit_returns_the_restored_session(monkeypatch):
    # read-mac needs the port, so the session is closed before the charge. The new
    # id has to come back with RATE_LIMITED or the port stays held by an orphan.
    class OpenSession:
        baud = 115200
        session_id = "old"

    class ReopenedSession:
        baud = 115200
        session_id = "new"

    closed: list[str] = []
    opened: list[tuple] = []
    _patch_esp_flash(monkeypatch, Config(allow_flash=True, sketch_roots=("/s",), max_uploads_per_hour=1))
    monkeypatch.setattr(server.backup, "identify", lambda port: {"mac": "24:0a:c4:aa:bb:cc", "chip": "ESP32"})
    monkeypatch.setattr(server.sessions, "by_port", lambda port: OpenSession())
    monkeypatch.setattr(server.sessions, "close_port", lambda port: closed.append(port))
    monkeypatch.setattr(
        server.sessions,
        "open",
        lambda *args, **kwargs: opened.append(args) or ReopenedSession(),
    )

    first = server.upload_sketch("/s/x", ESP32["port"], ESP32["suggested_fqbn"], "t", confirm=True)
    assert first["session_id"] == "new"
    closed.clear()
    opened.clear()

    refused = server.upload_sketch("/s/x", ESP32["port"], ESP32["suggested_fqbn"], "t", confirm=True)
    assert refused["error"]["code"] == errors.RATE_LIMITED
    assert refused["session_id"] == "new"
    assert closed == [ESP32["port"]]
    assert opened == [(ESP32["port"], 115200)]
