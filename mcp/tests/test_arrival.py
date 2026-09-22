import subprocess
from datetime import UTC, datetime, timedelta

import pytest

from omarchy_hardware import arrival, journal, project

PORT = "/dev/ttyACM0"
UNO = {
    "port": PORT, "vid": "2341", "pid": "0043", "serial": "A1", "board_type": "arduino_uno",
    "friendly_name": "Arduino Uno", "suggested_fqbn": "arduino:avr:uno", "suggested_baud": 9600,
}
NOW = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)


@pytest.fixture
def connected(monkeypatch):
    monkeypatch.setattr(arrival, "enumerate_boards", lambda: [UNO])


def test_new_board_message(connected):
    summary, body = arrival.message(arrival.describe(PORT), NOW)
    assert summary == "Arduino Uno"
    assert body == "Plugged in on ttyACM0.\nNew board, no history yet."


def test_known_board_message_names_label_and_last_flash(connected):
    journal.set_label(UNO, "greenhouse-node")
    journal.record_upload(UNO, fqbn="arduino:avr:uno", sketch_dir="/s/greenhouse", artifact_digest="d")
    info = arrival.describe(PORT)
    info["history"]["last_upload"]["at"] = (NOW - timedelta(days=12, hours=3)).isoformat()
    summary, body = arrival.message(info, NOW)
    assert summary == "greenhouse-node (Arduino Uno)"
    assert "Last flashed 12 days ago with greenhouse." in body


def test_device_strings_are_escaped_for_markup(monkeypatch):
    evil = {**UNO, "friendly_name": '<a href="x">click</a>\nNew line', "suggested_fqbn": None}
    monkeypatch.setattr(arrival, "enumerate_boards", lambda: [evil])
    summary, body = arrival.message(arrival.describe(PORT), NOW)
    assert "<a" not in summary and "&lt;a" in summary and "\n" not in summary
    assert "No pin profile for this board." in body


@pytest.mark.parametrize(
    ("delta", "text"),
    [(timedelta(seconds=5), "just now"), (timedelta(minutes=1), "1 minute ago"), (timedelta(hours=3), "3 hours ago")],
)
def test_ago(delta, text):
    assert arrival._ago((NOW - delta).isoformat(), NOW) == text
    assert arrival._ago("garbage", NOW) == "at an unknown time"


def test_notify_passes_text_after_the_option_terminator(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="claude\n", stderr="")

    monkeypatch.setattr(arrival.subprocess, "run", fake_run)
    choice = arrival.notify("--help", "--action=x=y", [("claude", "Start with Claude")])
    assert choice == "claude"
    argv = calls[0]
    assert argv[0] == arrival.NOTIFY_SEND
    assert argv[-3:] == ["--", "--help", "--action=x=y"]
    assert "--action=claude=Start with Claude" in argv and "--wait" in argv


def test_notify_ignores_unknown_or_missing_choices(monkeypatch):
    monkeypatch.setattr(
        arrival.subprocess, "run",
        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, stdout="rm -rf\n", stderr=""),
    )
    assert arrival.notify("s", "b", [("claude", "Start")]) == ""

    def timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr(arrival.subprocess, "run", timeout)
    assert arrival.notify("s", "b", [("claude", "Start")]) == ""


def test_monitor_is_offered_only_with_arduino_cli(monkeypatch, tmp_path):
    monkeypatch.setattr(arrival, "ARDUINO_CLI", str(tmp_path / "missing"))
    assert [key for key, _ in arrival.actions()] == ["claude"]
    cli = tmp_path / "arduino-cli"
    cli.write_text("#!/bin/sh\n")
    cli.chmod(0o755)
    monkeypatch.setattr(arrival, "ARDUINO_CLI", str(cli))
    assert [key for key, _ in arrival.actions()] == ["claude", "monitor"]


def test_main_dispatches_the_chosen_action(connected, monkeypatch, tmp_path):
    monkeypatch.setenv("OMARCHY_HARDWARE_PROJECTS", str(tmp_path / "projects"))
    started, monitors = [], []
    monkeypatch.setattr(project, "main", lambda args: started.append(args) or 0)
    monkeypatch.setattr(arrival, "open_monitor", lambda port, baud: monitors.append((port, baud)))

    monkeypatch.setattr(arrival, "notify", lambda *a: "claude")
    assert arrival.main([PORT]) == 0 and started == [[PORT]]

    monkeypatch.setattr(arrival, "notify", lambda *a: "monitor")
    assert arrival.main([PORT]) == 0 and monitors == [(PORT, 9600)]

    monkeypatch.setattr(arrival, "notify", lambda *a: "")
    assert arrival.main([PORT]) == 0 and len(started) == 1 and len(monitors) == 1


def test_main_rejects_bad_ports_and_ignores_vanished_boards(monkeypatch):
    shown = []
    monkeypatch.setattr(arrival, "notify", lambda *a: shown.append(a) or "")
    assert arrival.main(["/dev/ttyACM0; id"]) == 2
    monkeypatch.setattr(arrival, "enumerate_boards", lambda: [])
    assert arrival.main([PORT]) == 0
    assert shown == []


def test_monitor_command_is_a_fixed_argv(monkeypatch, tmp_path):
    monkeypatch.setenv("OMARCHY_HARDWARE_PROJECTS", str(tmp_path))
    spawned = []
    monkeypatch.setattr(project, "spawn", lambda argv, cwd=None: spawned.append(argv))
    arrival.open_monitor(PORT, 9600)
    assert spawned[0][-6:] == [arrival.ARDUINO_CLI, "monitor", "-p", PORT, "--config", "baudrate=9600"]


def test_monitor_needs_an_absolute_arduino_cli(monkeypatch, tmp_path):
    cli = tmp_path / "arduino-cli"
    cli.write_text("#!/bin/sh\n")
    cli.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(arrival, "ARDUINO_CLI", "arduino-cli")
    assert [key for key, _ in arrival.actions()] == ["claude"]
    spawned = []
    monkeypatch.setattr(arrival.project, "spawn", spawned.append)
    arrival.open_monitor("/dev/ttyACM0", 9600)
    assert spawned == []
