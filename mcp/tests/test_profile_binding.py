"""Binding tests use synthetic board identity and private journal files only."""

import json

import pytest

from omarchy_hardware import board_profiles, boards, errors, journal, server

BOARD = {"port": "/dev/ttyACM0", "vid": "303a", "pid": "1001", "serial": "binding-test",
         "board_type": "esp32_native_usb", "suggested_fqbn": None, "friendly_name": "ESP32 (native USB)"}
CHIP = {"chip": "ESP32-S3", "chip_detail": "ESP32-S3", "mac": "aa:bb:cc:dd:ee:ff",
        "flash_mb": 4, "psram_mb": 2}
PROFILE = "lilygo_t_qt_pro"


@pytest.fixture
def board(monkeypatch):
    result = dict(BOARD)
    monkeypatch.setattr(server, "_connected_board", lambda port: result)
    return result


def test_bind_clear_persist_and_audit(board, monkeypatch):
    journal.set_chip(board, CHIP)
    journal.set_label(board, "bench")
    notes = []
    monkeypatch.setattr(server.audit, "note", lambda event, **kw: notes.append((event, kw)))
    result = server.board_profile_assign(board["port"], PROFILE, True)
    assert result["ok"] and result["history"]["profile_id"] == PROFILE
    assert journal.history({**board, "port": "/dev/ttyACM1"})["profile_id"] == PROFILE
    assert journal.display_info(board)["profile_id"] == PROFILE
    entry = json.loads(next(journal.journal_dir().glob("*.json")).read_text())
    assert entry["schema"] == 1 and entry["profile_id"] == PROFILE
    assert server.board_profile_assign(board["port"], "", True)["ok"]
    assert journal.history(board)["profile_id"] is None
    assert journal.history(board)["label"] == "bench"
    assert len(notes) == 2 and notes[-1][1]["profile_id"] is None


@pytest.mark.parametrize(("change", "profile", "record", "confirm", "code"), [
    ({"serial": None}, PROFILE, True, True, errors.JOURNAL_UNAVAILABLE),
    ({}, "missing", True, True, errors.PROFILE_NOT_FOUND),
    ({}, "arduino_uno_r3", True, True, errors.INVALID_ARGUMENT),
    ({"vid": "2341", "pid": "0043"}, PROFILE, True, True, errors.BOARD_MISMATCH),
    ({}, PROFILE, False, True, errors.PROFILE_NOT_FOUND),
    ({}, PROFILE, True, False, errors.UNCONFIRMED),
    ({}, "", False, False, errors.UNCONFIRMED),
])
def test_binding_refusals_do_not_write(board, change, profile, record, confirm, code):
    board.update(change)
    if record and journal.board_key(board):
        journal.set_chip(board, CHIP)
    result = server.board_profile_assign(board["port"], profile, confirm)
    assert result["error"]["code"] == code
    assert journal.history(board).get("profile_id") is None


@pytest.mark.parametrize("chip", [{**CHIP, "chip": "ESP32-C3"}, {**CHIP, "flash_mb": 8}, {**CHIP, "psram_mb": 8}])
def test_binding_rejects_chip_and_memory_mismatch(board, chip):
    journal.set_chip(board, chip)
    result = server.board_profile_assign(board["port"], PROFILE, True)
    assert result["error"]["code"] == errors.BOARD_MISMATCH
    assert "Candidate profile ids:" in result["error"]["hint"]
    assert "board_probe" in result["error"]["hint"]


def test_unknown_memory_does_not_invent_a_mismatch(board):
    journal.set_chip(board, {**CHIP, "flash_mb": None, "psram_mb": None})
    assert server.board_profile_assign(board["port"], PROFILE, True)["ok"]


def test_clear_works_without_chip_or_existing_profile(board):
    journal.set_profile(board, "removed_profile")
    assert server.board_profile_assign(board["port"], "", True)["ok"]


def test_binding_is_used_for_profile_wiring_and_describe(board, monkeypatch):
    journal.set_chip(board, CHIP)
    journal.set_profile(board, PROFILE)
    monkeypatch.setattr(server.sessions, "by_port", lambda port: None)
    assert server.board_profile(port=board["port"])["profile"]["id"] == PROFILE
    result = server.wiring_check([{"pin": "GPIO6", "part": "test", "role": "digital_out"}], port=board["port"])
    assert result["verdict"] == "fail"
    assert server.describe_board(board["port"])["board"]["profile_id"] == PROFILE


def test_unbound_native_profile_hints_candidates(board):
    result = server.board_profile(port=board["port"])
    assert result["error"]["code"] == errors.PROFILE_NOT_FOUND
    assert PROFILE in result["error"]["hint"] and "board_probe" in result["error"]["hint"]


def test_panel_loads_one_profile_only_and_never_mutates_discovery(board, monkeypatch):
    journal.set_profile(board, PROFILE)
    original = dict(board)
    monkeypatch.setattr(board_profiles, "_load_all", lambda: pytest.fail("must not load all profiles"))
    view = boards.with_labels([board])[0]
    assert view["profile_id"] == PROFILE and view["profile_name"] == "LilyGO T-QT Pro"
    assert view["display"] == "GC9107 128x128"
    assert view["suggested_fqbn"] == "esp32:esp32:esp32s3:CDCOnBoot=cdc,FlashSize=4M,PSRAM=enabled"
    assert board == original


def test_panel_single_claim_and_absent_display(board):
    journal.set_profile(board, "esp32s3_devkitc1")
    view = boards.with_labels([board])[0]
    assert view["suggested_fqbn"] == "esp32:esp32:esp32s3" and "display" not in view


@pytest.mark.parametrize("profile", ["missing", "../../etc/passwd", "x" * 81, ["bad"]])
def test_invalid_or_missing_binding_is_ignored(board, profile):
    journal.set_label(board, "bench")
    path = next(journal.journal_dir().glob("*.json"))
    data = json.loads(path.read_text())
    data["profile_id"] = profile
    path.write_text(json.dumps(data))
    view = boards.with_labels([board])[0]
    assert "profile_id" not in view and view["label"] == "bench"
    assert server.board_profile(port=board["port"])["error"]["code"] == errors.PROFILE_NOT_FOUND


def test_journal_rejects_unsafe_profile_id(board):
    with pytest.raises(errors.ToolError):
        journal.set_profile(board, "../other")


def test_binding_audit_failure_is_reported_after_save(board, monkeypatch):
    journal.set_chip(board, CHIP)

    def fail(*args, **kwargs):
        raise OSError("audit read-only")

    monkeypatch.setattr(server.audit, "note", fail)
    result = server.board_profile_assign(board["port"], PROFILE, True)
    assert result["ok"] and result["audit_warning"] and journal.history(board)["profile_id"] == PROFILE
