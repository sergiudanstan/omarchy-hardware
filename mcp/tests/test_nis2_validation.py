"""Reject absent evidence and unsupported success claims in the evidence runner."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from hardware_validation import run_nis2
from omarchy_hardware import audit, config


@pytest.mark.parametrize("reports", [
    [{}], [{"checks": []}], [{"checks": [{"check": "x", "passed": 1}]}],
    [{"checks": [{"check": "x", "passed": "true"}]}],
    [{"checks": [{"check": "x", "passed": False}]}],
    [{"checks": [{"check": "x", "passed": True}] * 2}],
])
def test_malformed_or_failed_reports_are_not_passed(reports):
    assert run_nis2.hardware_results(reports)[0] == run_nis2.FAIL


def test_historical_report_is_not_current_hardware_verification():
    assert run_nis2.hardware_results([{ "checks": [{"check": "x", "passed": True}]}])[0] == run_nis2.LIMIT


def test_unknown_status_is_failed():
    evidence = run_nis2.Evidence()
    evidence.run("fixture", "fixture", "fixture", "fixture", lambda: ("passed", {}))
    assert evidence.checks[0]["status"] == run_nis2.FAIL


@pytest.mark.parametrize("check_id", ["NIS2-B-1", "NIS2-B-3", "NIS2-D-3", "NIS2-D-6", "NIS2-E-2"])
def test_runner_rejects_missing_or_failed_native_evidence(monkeypatch, tmp_path, check_id):
    (tmp_path / "SECURITY.md").write_text("fixture")
    log = tmp_path / "audit.log"
    log.write_text("")
    monkeypatch.setattr(audit, "log_path", lambda: log)
    monkeypatch.setattr(config, "load", lambda: config.Config())
    monkeypatch.setattr(config, "read_raw", lambda: {})
    # A misleading 'Good signature' diagnostic with a failing native exit status.
    def shell(*args, **kwargs):
        if "download" in args:
            # A partial download remains on disk despite a failing exit status.
            (Path(args[args.index("-D") + 1]) / "fixture.whl").write_text("public fixture")
        if "attestation" in args:
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 1, "", "Good signature")

    monkeypatch.setattr(run_nis2, "sh", shell)
    original = run_nis2.Evidence.run

    def selected(self, identity, article, control, method, fn):
        if identity == check_id:
            original(self, identity, article, control, method, fn)

    monkeypatch.setattr(run_nis2.Evidence, "run", selected)
    out = tmp_path / "results.json"
    monkeypatch.setattr(sys, "argv", ["run_nis2", "--plugin-dir", str(tmp_path), "--repo", str(tmp_path),
                                     "--release", "fixture", "--out", str(out)])
    with pytest.raises(SystemExit) as caught:
        run_nis2.main()
    assert caught.value.code == 1
    report = json.loads(out.read_text())
    assert report["summary"]["fail"] == 1
    assert report["checks"][0]["id"] == check_id
