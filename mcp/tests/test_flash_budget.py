import time

import pytest

from omarchy_hardware import config as config_module
from omarchy_hardware import errors, policy, server
from omarchy_hardware.config import Config

BOARD = {"port": "/dev/ttyACM0", "vid": "2341", "pid": "0043", "serial": "A1", "board_type": "arduino_uno",
         "suggested_fqbn": "arduino:avr:uno"}


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
    monkeypatch.setattr(server.flash, "upload_sketch", lambda *a, **k: calls.append(1) or {"ok": False})

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
