"""Native USB identity gates; fake runners and synthetic board dictionaries only."""

from types import SimpleNamespace

import pytest

from omarchy_hardware import boards, errors, fingerprint, ids, journal, server
from omarchy_hardware.config import Config

NATIVE = {"port": "/dev/ttyACM0", "vid": "303a", "pid": "1001", "serial": "S1",
          "board_type": ids.ESP_NATIVE_USB, "friendly_name": "ESP32 (native USB)",
          "suggested_fqbn": None, "busy": False, "holder_pids": []}
INFO = {"chip": "ESP32-S3", "chip_detail": "ESP32-S3 (QFN56)", "mac": "34:b7:da:5a:e4:88",
        "flash_mb": 4, "psram_mb": 2, "usb_mode": "USB-Serial/JTAG"}
FQBN = ids.ESP_CHIP_FQBNS["ESP32-S3"]


@pytest.fixture
def connected(monkeypatch):
    board = dict(NATIVE)
    monkeypatch.setattr(server, "enumerate_boards", lambda: [board])
    monkeypatch.setattr(server, "enumerate_rp2_devices", lambda: [])
    monkeypatch.setattr(server.policy, "resolve_port", lambda port: port)
    monkeypatch.setattr(server.policy, "resolve_flash_target", lambda port: port)
    monkeypatch.setattr(server.policy, "check_readable", lambda port: None)
    monkeypatch.setattr(server.sessions, "by_port", lambda port: None)
    monkeypatch.setattr(server, "fingerprints", fingerprint.Cache())
    monkeypatch.setattr(server.espchip, "probe", lambda port: dict(INFO))
    return board


@pytest.mark.parametrize("pid", ["1001", "0002"])
def test_native_ids_have_no_usb_chip_guess(pid):
    info = ids.identify("303a", pid)
    assert info.board_type == ids.ESP_NATIVE_USB and info.fqbn is None
    assert ("303a", pid) not in ids.CHIPS
    assert info.friendly_name not in boards.supported_board_names()


def test_probe_records_names_and_describe_exposes_block(connected):
    journal.set_label(connected, "bench")
    result = server.board_probe(connected["port"])
    assert result == {"ok": True, "port": connected["port"], **INFO, "suggested_fqbn": FQBN, "tracked": True}
    recorded = journal.history(connected)["chip"]
    assert recorded["family"] == "ESP32-S3" and recorded["detail"] == INFO["chip_detail"]
    assert recorded["mac"] == INFO["mac"] and recorded["probed_at"]
    view = boards.with_labels([connected])[0]
    assert view["label"] == "bench" and view["chip"] == "ESP32-S3"
    assert view["friendly_name"] == "ESP32-S3 (native USB)" and view["suggested_fqbn"] == FQBN
    assert connected["suggested_fqbn"] is None
    assert server.list_boards()["boards"][0] == view
    detail = server.describe_board(connected["port"])["board"]
    assert detail["chip"] == recorded and detail["profile_id"] is None
    refused = server.board_profile(port=connected["port"])
    assert refused["error"]["code"] == errors.PROFILE_NOT_FOUND
    assert "profile_id" in refused["error"]["hint"]


def test_display_reads_once_and_old_schema_loads(connected, monkeypatch):
    journal.set_label(connected, "old entry")
    calls = []
    read = journal._read

    def counted(path):
        calls.append(path)
        return read(path)

    monkeypatch.setattr(journal, "_read", counted)
    assert journal.display_info(connected) == {"label": "old entry", "chip": None}
    assert len(calls) == 1
    assert journal.history(connected)["chip"] is None
    calls[0].write_text('{"schema": 1, "uploads": [], "chip": ["bad"]}')
    assert boards.with_labels([connected])[0]["chip"] is None
    calls[0].write_text('not JSON')
    assert journal.display_info(connected) == {"label": None, "chip": None}


def test_untracked_probe_only_caches_result(connected):
    connected.update(vid="1a86", pid="7523", board_type="unknown", serial=None)
    result = server.board_probe(connected["port"])
    assert result["ok"] and result["tracked"] is False
    assert not journal.journal_dir().exists()
    assert server.fingerprints.accepts(connected, FQBN)["chip"] == "ESP32-S3"
    assert server._esp_board(connected["port"]) == connected


def test_probe_cache_expires_and_does_not_follow_another_adapter(connected, monkeypatch):
    server.board_probe(connected["port"])
    assert server.fingerprints.accepts({**connected, "serial": "other"}, FQBN) is None
    later = fingerprint.time.monotonic() + fingerprint.CACHE_SECONDS + 1
    monkeypatch.setattr(fingerprint.time, "monotonic", lambda: later)
    assert server.fingerprints.accepts(connected, FQBN) is None


def test_probe_refuses_other_devices_and_busy_ports(connected, monkeypatch):
    monkeypatch.setattr(server.espchip, "probe", lambda port: pytest.fail("must not probe"))
    connected.update(vid="2341", pid="0043", board_type="arduino_uno", suggested_fqbn="arduino:avr:uno")
    assert server.board_probe(connected["port"])["error"]["code"] == errors.UNSUPPORTED_OPERATION
    connected.update(NATIVE)
    connected.update(busy=True, holder_pids=[1])
    assert server.board_probe(connected["port"])["error"]["code"] == errors.PORT_BUSY


def test_native_empty_fingerprint_suggests_probe_and_audits(connected, monkeypatch):
    events = []
    monkeypatch.setattr(server.sessions, "open", lambda *a: SimpleNamespace(session_id="s"))
    monkeypatch.setattr(server.sessions, "close", lambda *a: None)
    monkeypatch.setattr(fingerprint, "capture", lambda *a: b"noise")
    monkeypatch.setattr(server.audit, "note", lambda event, **kw: events.append(event))
    result = server.fingerprint_board(connected["port"])
    assert result["ok"] and not result["identified"] and "board_probe" in result["hint"]
    assert events == ["fingerprint_board"]


@pytest.fixture
def upload(connected, monkeypatch):
    events = []
    monkeypatch.setattr(server, "_config", lambda: Config(allow_flash=True, sketch_roots=("/s",)))
    monkeypatch.setattr(server.flash, "board_mcu", lambda fqbn: "esp32s3")
    monkeypatch.setattr(server, "_flash_budget_for", lambda config: SimpleNamespace(
        charge=lambda key: events.append(("budget", key))))
    monkeypatch.setattr(server.audit, "require", lambda event, *a, **kw: events.append(("audit", event)))
    monkeypatch.setattr(server, "_journal_upload", lambda *a: {})

    def identify(port):
        events.append(("identity", port))
        return dict(INFO)

    def write(*a, before_write, **kw):
        before_write()
        events.append(("write", a[1]))
        return {"ok": True}

    monkeypatch.setattr(server.espchip, "identify", identify)
    monkeypatch.setattr(server.flash, "upload_sketch", write)
    return events


def perform():
    return server.upload_sketch("/s/x", NATIVE["port"], FQBN, "token", confirm=True)


def test_live_match_orders_identity_audit_budget_write(upload):
    assert perform()["ok"]
    assert [e[0] for e in upload] == ["identity", "audit", "budget", "write"]
    assert INFO["mac"] in str(upload[2])


@pytest.mark.parametrize("failure", ["different_chip", "probe_error", "audit_error"])
def test_refusal_preserves_budget_and_restores_session(connected, upload, monkeypatch, failure):
    journal.set_chip(connected, INFO)  # Persisted S3 is deliberately not authority.
    monkeypatch.setattr(server.sessions, "by_port", lambda port: SimpleNamespace(baud=115200))
    monkeypatch.setattr(server.sessions, "close_port", lambda port: upload.append(("close", port)))
    monkeypatch.setattr(server.sessions, "open", lambda *a, **kw: SimpleNamespace(session_id="restored"))
    if failure == "different_chip":
        monkeypatch.setattr(server.espchip, "identify", lambda port: {**INFO, "chip": "ESP32-C3"})
        code = errors.BOARD_MISMATCH
    elif failure == "probe_error":
        def fail(port):
            raise errors.ToolError(errors.SERIAL_ERROR, "timeout")
        monkeypatch.setattr(server.espchip, "identify", fail)
        code = errors.UNKNOWN_BOARD
    else:
        def fail(*a, **kw):
            raise errors.ToolError(errors.AUDIT_LOG_FAILED, "audit failure")
        monkeypatch.setattr(server.audit, "require", fail)
        code = errors.AUDIT_LOG_FAILED
    result = perform()
    assert result["error"]["code"] == code
    assert result["session_restored"] and result["session_id"] == "restored"
    assert "close" in [e[0] for e in upload]
    assert not {"budget", "write"} & {e[0] for e in upload}
    if failure == "different_chip":
        assert "ESP32-C3" in result["error"]["message"] and "esp32s3" in result["error"]["message"]


@pytest.mark.parametrize("mcu", [None, "atmega328p"])
def test_non_esp_fqbn_refused_without_probe(upload, monkeypatch, mcu):
    monkeypatch.setattr(server.flash, "board_mcu", lambda fqbn: mcu)
    assert perform()["error"]["code"] == errors.BOARD_MISMATCH
    assert upload == []


@pytest.mark.parametrize("vid,pid,kind,fqbn", [
    ("2341", "0043", "arduino_uno", "arduino:avr:uno"),
    ("2341", "0070", "arduino_nano_esp32", "arduino:esp32:nano_nora"),
])
def test_real_fqbn_boards_keep_existing_gate(connected, upload, vid, pid, kind, fqbn):
    connected.update(vid=vid, pid=pid, board_type=kind, suggested_fqbn=fqbn)
    assert server.upload_sketch("/s/x", connected["port"], fqbn, "t", confirm=True)["ok"]
    assert [e[0] for e in upload] == ["budget", "write"]


def test_probe_of_bridge_still_requires_fingerprinted_opt_in(connected, upload, monkeypatch):
    connected.update(vid="10c4", pid="ea60", board_type="unknown")
    server.board_probe(connected["port"])
    assert perform()["error"]["code"] == errors.UNKNOWN_BOARD
    monkeypatch.setattr(server, "_config", lambda: Config(allow_flash=True, sketch_roots=("/s",),
                                                        allow_fingerprinted=True))
    monkeypatch.setattr(server.backup, "identify", lambda port: INFO)
    assert perform()["ok"]
    assert upload[0] == ("audit", "upload_identity_fingerprint")


def test_disabled_flash_hints_name_user_settings(monkeypatch):
    monkeypatch.setattr(server, "_config", Config)
    for result in (server.compile_sketch("/s/x", FQBN), perform(), server.firmware_restore(NATIVE["port"], "b")):
        assert result["error"]["code"] == errors.FLASH_DISABLED
        hint = result["error"]["hint"]
        assert "user" in hint and "[flash] allow = true" in hint and "sketch_roots" in hint
        assert str(server.CONFIG_PATH) in hint


def test_probe_audit_is_best_effort(connected, monkeypatch):
    def unavailable(*a, **kw):
        raise OSError("not writable")

    monkeypatch.setattr(server.audit, "note", unavailable)
    assert server.board_probe(connected["port"])["ok"]


def test_classic_probe_accepts_the_same_fqbns_as_rom(connected):
    server.fingerprints.store_probe(connected, {**INFO, "chip": "ESP32"})
    assert server.fingerprints.accepts(connected, "esp32:esp32:esp32doit-devkit-v1")


def test_token_without_port_serial_has_recompile_hint():
    from omarchy_hardware import flash

    token = flash.mint_token("/s/x", FQBN)
    with pytest.raises(errors.ToolError) as exc:
        flash.verify_token(token, "/s/x", FQBN, serial="S1")
    assert exc.value.code == errors.INVALID_TOKEN
    assert "port=" in exc.value.hint


def test_native_family_allows_serial_writes(connected):
    server._require_serial_write_target(connected["port"], Config(allow_unknown_serial=False))
