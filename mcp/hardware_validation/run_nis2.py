"""NIS2 (EU 2022/2555) technical evidence run for omarchy-hardware.

NIS2 obliges organisations, not products. This produces evidence that the
plugin's controls support the risk-management measures of Article 21(2)(a)-(j)
and the incident reporting of Article 23; it does not certify anyone as
compliant. docs/nis2.md maps every check ID below to its article and control.

Each check ends as pass, fail, not_applicable (the measure does not apply to a
single-user desktop plugin) or limitation (a gap that is known and stated).
Not part of pytest: it reads the real config and audit log, talks to the local
MING stack and to GitHub. It changes nothing except temporary files.

    python mcp/hardware_validation/run_nis2.py \
        --plugin-dir ~/.config/omarchy/plugins/io.github.sergiudanstan.hardware \
        --repo . --release v0.1.5 --uno-results uno.json uno-extended.json --out nis2.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

GH_REPO = "sergiudanstan/omarchy-hardware"
PASS, FAIL, NA, LIMIT = "pass", "fail", "not_applicable", "limitation"


class Evidence:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []

    def run(self, check_id: str, article: str, control: str, method: str, fn: Callable[[], tuple[str, Any]]) -> None:
        try:
            status, evidence = fn()
        except Exception as exc:  # noqa: BLE001 - a check that crashes is a failed check, with the reason
            status, evidence = FAIL, f"{type(exc).__name__}: {exc}"[:500]
        self.checks.append({"id": check_id, "article": article, "control": control, "method": method,
                            "status": status, "evidence": evidence})
        print(f"{status.upper():15} {check_id}  {control}", file=sys.stderr)


def sh(*argv: str, cwd: Path | None = None, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603


def gh_json(path: str) -> Any:
    result = sh("gh", "api", path)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[:300])
    return json.loads(result.stdout)


def main() -> None:  # noqa: C901 - one flat list of checks reads better than indirection
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin-dir", required=True, help="the installed plugin checkout under test")
    parser.add_argument("--repo", required=True, help="a development checkout with .dev-venv for the test suite")
    parser.add_argument("--release", required=True, help="the release tag to verify, e.g. v0.1.5")
    parser.add_argument("--uno-results", nargs="*", default=[], help="JSON results of run_uno*.py")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    plugin = Path(args.plugin_dir).expanduser().resolve()
    repo = Path(args.repo).expanduser().resolve()
    sys.path.insert(0, str(plugin / "mcp"))

    from omarchy_hardware import (  # noqa: F401
        audit,
        board_profiles,
        config,
        errors,
        flash,
        http_lite,
        journal,
        ming,
        mqtt_lite,
        policy,
        support,
    )
    from omarchy_hardware.boards import enumerate_boards
    from omarchy_hardware.errors import ToolError

    ev = Evidence()
    cfg = config.load()
    raw_cfg = config.read_raw() or {}
    security_md = (plugin / "SECURITY.md").read_text(encoding="utf-8")
    uno_runs = [json.loads(Path(p).read_text(encoding="utf-8")) for p in args.uno_results]
    audit_lines = [json.loads(line) for line in audit.log_path().read_text(encoding="utf-8").splitlines() if line]

    def refused(fn: Callable[[], Any], code: str | None = None) -> tuple[bool, str]:
        try:
            fn()
        except ToolError as exc:
            return (code is None or exc.code == code), f"{exc.code}: {exc.message}"
        return False, "accepted"

    # ------------------------------------------------------------------ (a) risk analysis and policies
    def a1():
        missing = [p for p in ("docs/threat-model.md", "SECURITY.md") if not (plugin / p).is_file()]
        return (FAIL if missing else PASS), {"missing": missing}
    ev.run("NIS2-A-1", "21(2)(a)", "Documented threat model and security policy", "files present in the release", a1)

    def a2():
        dev_python = repo / ".dev-venv" / "bin" / "python"
        result = sh(str(dev_python), ".github/scripts/check_claims.py", cwd=repo, timeout=600)
        return (PASS if result.returncode == 0 else FAIL), (result.stdout + result.stderr).strip().splitlines()[:2]
    ev.run("NIS2-A-2", "21(2)(a)", "Threat-model claims match the code", "check_claims.py", a2)

    # ------------------------------------------------------------------ (b) incident handling, Art. 23
    def b1():
        result = audit.verify()
        return (PASS if result.get("ok") else FAIL), {k: v for k, v in result.items() if k != "path"}
    ev.run("NIS2-B-1", "21(2)(b)", "Hash-chained audit log verifies end to end", "audit.verify on the real log", b1)

    def b2():
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "audit.log"
            shutil.copyfile(audit.log_path(), copy)
            lines = copy.read_text(encoding="utf-8").splitlines(keepends=True)
            middle = len(lines) // 2
            lines[middle] = lines[middle].replace('"event":"', '"event":"x', 1)
            copy.write_text("".join(lines), encoding="utf-8")
            result = audit.verify(copy)
        detected = result.get("ok") is False and result.get("broken_at_line") == middle + 1
        return (PASS if detected else FAIL), {"edited_line": middle + 1, "reported": result.get("broken_at_line")}
    ev.run("NIS2-B-2", "21(2)(b)", "An edited record is detected at its line", "tamper a temporary copy", b2)

    def b3():
        started = sum(1 for e in audit_lines if e.get("event") == "upload_started")
        finished = sum(1 for e in audit_lines if e.get("event") == "upload_finished")
        kinds = sorted({e.get("event") for e in audit_lines})
        timed = all({"at", "event"} <= set(e) for e in audit_lines)
        # pid was added to every record on 2026-09-18 (f539b9e); older records predate it.
        no_pid = [e for e in audit_lines if "pid" not in e]
        newest_without_pid = max((e["at"] for e in no_pid), default=None)
        first_with_pid = min((e["at"] for e in audit_lines if "pid" in e), default=None)
        pid_ok = not no_pid or (first_with_pid is not None and newest_without_pid < first_with_pid)
        ok = started == finished and timed and pid_ok
        return (PASS if ok else FAIL), {"upload_started": started, "upload_finished": finished,
                                        "every_record_has_time_and_event": timed,
                                        "records_without_pid": len(no_pid),
                                        "all_of_them_predate_the_pid_field": pid_ok, "event_kinds": kinds}
    ev.run("NIS2-B-3", "21(2)(b), 23", "Actuations are logged with time, process and outcome",
           "audit log contents", b3)

    def b4():
        channel = "security/advisories/new" in security_md
        times = bool(re.search(r"Acknowledgement\s*\|\s*within \d+", security_md))
        return (PASS if channel and times else FAIL), {"private_channel": channel, "response_targets": times}
    ev.run("NIS2-B-4", "21(2)(b)", "Private vulnerability reporting with response targets", "SECURITY.md", b4)

    def b5():
        polluted = [e for e in audit_lines if e.get("backup_id") == "20260919-120000-aaaaaaaaaaaa"]
        if not polluted:
            return PASS, "no test records in the real log"
        return LIMIT, {"test_records": len(polluted),
                       "note": "A test wrote these into the real log from 2026-09-19 until the isolation fix; "
                               "they stay, because removing them would break the hash chain."}
    ev.run("NIS2-B-5", "23", "The audit log holds only real events", "scan for known test records", b5)

    # ------------------------------------------------------------------ (c) continuity and backups
    def c1():
        unos = [b for b in enumerate_boards() if b.get("board_type") != "unknown"]
        if not unos:
            return LIMIT, "no identified board connected"
        uploads = journal.history(unos[0]).get("uploads") or []
        return (PASS if uploads else FAIL), {"board": unos[0].get("friendly_name"), "recorded_uploads": len(uploads)}
    ev.run("NIS2-C-1", "21(2)(c)", "What was flashed to each board is recorded", "board journal", c1)

    def c2():
        pids = {e.get("pid") for e in audit_lines}
        ok = audit.verify().get("ok") and len(pids) >= 2
        return (PASS if ok else FAIL), {"processes_in_one_chain": len(pids)}
    ev.run("NIS2-C-2", "21(2)(c)", "The audit chain continues across server restarts", "distinct pids, one chain", c2)

    def c3():
        rows = {row["id"]: row for row in support.export("microcontroller")["microcontroller"]}
        backup, restore = rows.get("firmware.backup"), rows.get("firmware.restore")
        ok = bool(backup and restore and restore["requires_confirmation"])
        return (PASS if ok else FAIL), {"backup": backup and backup["availability"],
                                        "restore": restore and restore["availability"],
                                        "note": "ESP32 only; the Uno is not applicable (refusal verified on it)"}
    ev.run("NIS2-C-3", "21(2)(c)", "Firmware can be backed up and restored (ESP32)", "support matrix", c3)

    ev.run("NIS2-C-4", "21(2)(c)", "config.toml backup", "manual",
           lambda: (LIMIT, "No built-in config backup; the file is small and user-owned. Back it up with the "
                           "rest of ~/.config."))

    # ------------------------------------------------------------------ (d) supply chain
    def d1():
        text = (plugin / "mcp" / "requirements.lock").read_text(encoding="utf-8")
        blocks = re.split(r"\n(?=[A-Za-z0-9_.-]+==)", text.strip())
        pins = [b for b in blocks if re.match(r"[A-Za-z0-9_.-]+==", b)]
        unhashed = [b.split()[0] for b in pins if "--hash=sha256:" not in b]
        return (FAIL if unhashed or not pins else PASS), {"pinned": len(pins), "without_hash": unhashed}
    ev.run("NIS2-D-1", "21(2)(d)", "Every runtime dependency is version-pinned and hash-locked",
           "requirements.lock", d1)

    def d2():
        result = sh("uvx", "--from", "pip-audit", "pip-audit", "--strict", "--no-deps",
                    "-r", str(plugin / "mcp" / "requirements.lock"), "--progress-spinner", "off", timeout=600)
        tail = (result.stdout + result.stderr).strip().splitlines()[-2:]
        return (PASS if result.returncode == 0 else FAIL), tail
    ev.run("NIS2-D-2", "21(2)(d), (e)", "No known vulnerabilities in the locked dependencies", "pip-audit", d2)

    def d3():
        unpinned = []
        for workflow in sorted((repo / ".github" / "workflows").glob("*.yml")):
            for line in workflow.read_text(encoding="utf-8").splitlines():
                match = re.search(r"uses:\s*([^\s#]+)", line)
                if match and not re.search(r"@[0-9a-f]{40}$", match.group(1)):
                    unpinned.append(f"{workflow.name}: {match.group(1)}")
        return (FAIL if unpinned else PASS), {"unpinned": unpinned}
    ev.run("NIS2-D-3", "21(2)(d)", "CI actions are pinned to commit SHAs", "workflow files", d3)

    def d4():
        text = (repo / ".github" / "dependabot.yml").read_text(encoding="utf-8")
        ecosystems = re.findall(r"package-ecosystem:\s*(\S+)", text)
        return (PASS if {"pip", "github-actions"} <= set(ecosystems) else FAIL), {"ecosystems": ecosystems}
    ev.run("NIS2-D-4", "21(2)(d), (e)", "Dependency updates are automated", "dependabot.yml", d4)

    def d5():
        release = json.loads(sh("gh", "release", "view", args.release, "-R", GH_REPO, "--json", "assets").stdout)
        assets = [a["name"] for a in release["assets"]]
        sbom = [a for a in assets if a.endswith(".cdx.json")]
        return (PASS if sbom else FAIL), {"assets": assets}
    ev.run("NIS2-D-5", "21(2)(d)", "The release ships a CycloneDX SBOM", "release assets", d5)

    def d6():
        results = {}
        with tempfile.TemporaryDirectory() as tmp:
            sh("gh", "release", "download", args.release, "-R", GH_REPO, "-D", tmp,
               "-p", "*-plugin.tar.gz", "-p", "*.whl")
            for artifact in sorted(Path(tmp).iterdir()):
                verified = sh("gh", "attestation", "verify", str(artifact), "--repo", GH_REPO)
                results[artifact.name] = verified.returncode == 0
        return (PASS if results and all(results.values()) else FAIL), results
    ev.run("NIS2-D-6", "21(2)(d)", "Release artifacts carry verifiable build provenance", "gh attestation verify", d6)

    # ------------------------------------------------------------------ (e) secure development
    def e1():
        protection = gh_json(f"repos/{GH_REPO}/branches/main/protection")
        signed = protection.get("required_signatures", {}).get("enabled") is True
        checks = protection.get("required_status_checks", {}).get("contexts", [])
        conversations = protection.get("required_conversation_resolution", {}).get("enabled") is True
        ok = signed and len(checks) >= 5 and conversations
        return (PASS if ok else FAIL), {"signed_commits": signed, "required_checks": checks,
                                        "conversation_resolution": conversations}
    ev.run("NIS2-E-1", "21(2)(e)", "main requires signed commits, CI and resolved reviews", "branch protection", e1)

    def e2():
        result = sh("git", "tag", "-v", args.release, cwd=repo)
        good = "Good" in result.stderr
        return (PASS if good else FAIL), result.stderr.strip().splitlines()[:1]
    ev.run("NIS2-E-2", "21(2)(e)", "The release tag is signed", "git tag -v", e2)

    def e3():
        alerts = gh_json(f"repos/{GH_REPO}/code-scanning/alerts?state=open&tool_name=CodeQL")
        return (PASS if not alerts else FAIL), {"open_codeql_alerts": len(alerts)}
    ev.run("NIS2-E-3", "21(2)(e)", "No open CodeQL findings", "code scanning API", e3)

    def e4():
        runs = gh_json(f"repos/{GH_REPO}/actions/workflows/ci.yml/runs?branch=main&per_page=1")["workflow_runs"]
        conclusion = runs[0]["conclusion"] if runs else None
        return (PASS if conclusion == "success" else FAIL), {"latest_main_ci": conclusion}
    ev.run("NIS2-E-4", "21(2)(e)", "CI is green on main", "Actions API", e4)

    def e5():
        ok = "## Coordinated disclosure" in security_md and "90 days" in security_md
        return (PASS if ok else FAIL), "90-day coordinated disclosure policy" if ok else "missing"
    ev.run("NIS2-E-5", "21(2)(e)", "Coordinated vulnerability disclosure policy", "SECURITY.md", e5)

    def e6():
        alerts = gh_json(f"repos/{GH_REPO}/code-scanning/alerts?state=open&tool_name=Scorecard")
        rules = sorted(a["rule"]["id"] for a in alerts)
        return (PASS if not rules else LIMIT), {"open_scorecard_items": rules,
                                                "note": "single-maintainer project: Code-Review and Maintained "
                                                        "cannot pass; see docs/threat-model.md"}
    ev.run("NIS2-E-6", "21(2)(e), (f)", "OpenSSF Scorecard items", "code scanning API", e6)

    # ------------------------------------------------------------------ (f) effectiveness
    def f1():
        dev_python = repo / ".dev-venv" / "bin" / "python"
        result = sh(str(dev_python), "-m", "pytest", "mcp/tests", "-q", "-p", "no:cacheprovider", cwd=repo,
                    timeout=900)
        tail = result.stdout.strip().splitlines()[-1:]
        return (PASS if result.returncode == 0 else FAIL), tail
    ev.run("NIS2-F-1", "21(2)(f)", "The automated test suite passes", "pytest", f1)

    def f2():
        if not uno_runs:
            return LIMIT, "no hardware results supplied"
        summary = [{"checks": len(r["checks"]), "passed": sum(c["passed"] for c in r["checks"])} for r in uno_runs]
        ok = all(s["checks"] == s["passed"] for s in summary)
        return (PASS if ok else FAIL), summary
    ev.run("NIS2-F-2", "21(2)(f)", "Physical validation on real hardware passes", "run_uno*.py results", f2)

    # ------------------------------------------------------------------ (g) cyber hygiene
    def g1():
        path = config.CONFIG_PATH
        st = os.lstat(path)
        ok = stat.S_ISREG(st.st_mode) and stat.S_IMODE(st.st_mode) & 0o077 == 0 and st.st_uid == os.getuid()
        return (PASS if ok else FAIL), {"mode": oct(stat.S_IMODE(st.st_mode)),
                                        "owner_is_user": st.st_uid == os.getuid()}
    ev.run("NIS2-G-1", "21(2)(g)", "config.toml is private, owned and not a symlink", "lstat", g1)

    def g2():
        outcomes = {}
        real = config.CONFIG_PATH
        with tempfile.TemporaryDirectory() as tmp:
            loose = Path(tmp) / "loose.toml"
            loose.write_text("[pi]\n", encoding="utf-8")
            loose.chmod(0o644)
            link = Path(tmp) / "link.toml"
            link.symlink_to(loose)
            for name, path in (("group/world-readable", loose), ("symlink", link)):
                config.CONFIG_PATH = path
                try:
                    config.load()
                    outcomes[name] = "accepted"
                except config.ConfigError as exc:
                    outcomes[name] = f"refused: {exc}"[:120]
                finally:
                    config.CONFIG_PATH = real
        ok = all(v.startswith("refused") for v in outcomes.values())
        return (PASS if ok else FAIL), outcomes
    ev.run("NIS2-G-2", "21(2)(g)", "A readable or symlinked config is refused", "config.load on temp files", g2)

    def g3():
        files = []

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    if key.endswith("_file") and isinstance(value, str):
                        files.append(Path(value).expanduser())
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(raw_cfg)
        loose = [str(f) for f in files if not f.is_file() or stat.S_IMODE(f.stat().st_mode) & 0o077]
        return (FAIL if loose else PASS), {"credential_files": len(files), "not_private": loose}
    ev.run("NIS2-G-3", "21(2)(g)", "Credential files are private", "mode of every *_file", g3)

    def g4():
        inline = []

        def walk(node: Any, trail: str) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    here = f"{trail}.{key}" if trail else key
                    if re.search(r"(password|token|secret)$", key) and isinstance(value, str):
                        inline.append(here)
                    walk(value, here)
            elif isinstance(node, list):
                for index, item in enumerate(node):
                    walk(item, f"{trail}[{index}]")

        walk(raw_cfg, "")
        return (FAIL if inline else PASS), {"inline_secrets": inline, "rule": "only *_env and *_file references"}
    ev.run("NIS2-G-4", "21(2)(g)", "No secrets stored inline in config.toml", "config scan", g4)

    # ------------------------------------------------------------------ (h) cryptography
    def h1():
        with tempfile.TemporaryDirectory() as tmp:
            sketch = Path(tmp) / "s"
            artifact = Path(tmp) / "a"
            sketch.mkdir()
            artifact.mkdir()
            (artifact / "s.ino.hex").write_bytes(b":00000001FF\n")
            digest = flash._artifact_digest(str(artifact))
            token = flash.mint_token(str(sketch), "arduino:avr:uno", "", str(artifact), digest)
            signature, expiry = token.rsplit(".", 1)
            forged = ("0" if signature[0] != "0" else "1") + signature[1:] + "." + expiry
            genuine_ok, _ = refused(lambda: flash.verify_token(token, str(sketch), "arduino:avr:uno", "",
                                                               str(artifact), digest))
            forged_refused, why = refused(lambda: flash.verify_token(forged, str(sketch), "arduino:avr:uno", "",
                                                                     str(artifact), digest))
            other_board, why2 = refused(lambda: flash.verify_token(token, str(sketch), "arduino:avr:nano", "",
                                                                   str(artifact), digest))
        ok = not genuine_ok and forged_refused and other_board
        return (PASS if ok else FAIL), {"genuine_accepted": not genuine_ok, "forged": why, "other_board": why2}
    ev.run("NIS2-H-1", "21(2)(h)", "Upload authorisation is an HMAC that cannot be forged or reused",
           "flash.mint_token / verify_token", h1)

    broker = cfg.ming_mqtt[0] if cfg.ming_mqtt else None

    def mqtt_connect(**changes: Any) -> str:
        password = ming.read_secret(broker.security.password_env, broker.security.password_file, "mqtt")
        options = mqtt_lite.Options(host=changes.get("host", broker.host), port=broker.port, tls=True, timeout=5,
                                    ca_file=changes.get("ca_file", broker.security.ca_file),
                                    username=broker.security.username, password=password)
        try:
            with mqtt_lite.Client(options, max_packet=4096):
                return "connected"
        except mqtt_lite.MqttError as exc:
            return f"refused: {exc}"

    def h2():
        if broker is None:
            return LIMIT, "no MQTT broker configured"
        outcome = mqtt_connect()
        return (PASS if outcome == "connected" else FAIL), outcome
    ev.run("NIS2-H-2", "21(2)(h), (j)", "MQTT over TLS with the pinned CA connects", "mqtt_lite to the local broker",
           h2)

    def h3():
        if broker is None:
            return LIMIT, "no MQTT broker configured"
        outcomes = {"system_ca_instead_of_pinned": mqtt_connect(ca_file=None),
                    "wrong_hostname_127.0.0.2": mqtt_connect(host="127.0.0.2")}
        ok = all(v.startswith("refused") for v in outcomes.values())
        return (PASS if ok else FAIL), outcomes
    ev.run("NIS2-H-3", "21(2)(h), (j)", "TLS refuses an unknown CA and a wrong hostname", "mqtt_lite negative tests",
           h3)

    def h4():
        if not cfg.ming_influxdb:
            return LIMIT, "no InfluxDB configured"
        db = cfg.ming_influxdb[0]
        good = http_lite.request("GET", db.url + "/health", timeout=5, ca_file=db.security.ca_file).status
        try:
            http_lite.request("GET", db.url + "/health", timeout=5, ca_file=None)
            system_ca = "accepted"
        except http_lite.HttpError as exc:
            system_ca = f"refused: {exc}"
        ok = good == 200 and system_ca.startswith("refused")
        return (PASS if ok else FAIL), {"pinned_ca_status": good, "system_ca": system_ca}
    ev.run("NIS2-H-4", "21(2)(h), (j)", "HTTPS to InfluxDB is verified against the pinned CA", "http_lite", h4)

    def h5():
        source = (plugin / "mcp" / "omarchy_hardware" / "mqtt_lite.py").read_text(encoding="utf-8")
        source += (plugin / "mcp" / "omarchy_hardware" / "http_lite.py").read_text(encoding="utf-8")
        count = source.count("minimum_version = ssl.TLSVersion.TLSv1_2")
        return (PASS if count >= 2 else FAIL), {"tls12_minimum_set_in": count}
    ev.run("NIS2-H-5", "21(2)(h)", "TLS 1.2 is the minimum protocol version", "source", h5)

    # ------------------------------------------------------------------ (i) access control and assets
    def i1():
        outcomes = {p: refused(lambda p=p: policy.resolve_port(p))[1]
                    for p in ("/dev/ttyS4", "/dev/sda", "/dev/serial/by-id/../../sda")}
        ok = all(not v.startswith("accepted") and "PORT_NOT_ALLOWED" in v for v in outcomes.values())
        return (PASS if ok else FAIL), outcomes
    ev.run("NIS2-I-1", "21(2)(i)", "Only allowlisted serial devices can be opened", "policy.resolve_port", i1)

    def i2():
        ok, why = refused(lambda: flash.resolve_sketch_dir("/etc", cfg.sketch_roots))
        return (PASS if ok else FAIL), why
    ev.run("NIS2-I-2", "21(2)(i)", "Compile and upload stay inside sketch_roots", "flash.resolve_sketch_dir", i2)

    def i3():
        ok, why = refused(lambda: policy.check_host("evil.example.com", cfg), errors.HOST_NOT_ALLOWED)
        return (PASS if ok else FAIL), why
    ev.run("NIS2-I-3", "21(2)(i)", "Remote hosts outside the allowlist are refused", "policy.check_host", i3)

    def i4():
        budget = policy.WriteBudget(10)
        budget.charge("port", 10)
        ok, why = refused(lambda: budget.charge("port", 1), errors.RATE_LIMITED)
        return (PASS if ok else FAIL), why
    ev.run("NIS2-I-4", "21(2)(i)", "Writes are rate-limited per target", "policy.WriteBudget", i4)

    def i5():
        wanted = {
            "serial_open on a non-allowlisted port is refused",
            "upload_sketch without confirm is refused",
            "serial_write without confirm is refused",
            "compile_sketch outside sketch_roots is refused",
            "hardware_report redacts the USB serial",
        }
        seen = {c["check"]: c["passed"] for r in uno_runs for c in r["checks"] if c["check"] in wanted}
        if not seen:
            return LIMIT, "no hardware results supplied"
        ok = set(seen) == wanted and all(seen.values())
        return (PASS if ok else FAIL), seen
    ev.run("NIS2-I-5", "21(2)(i)", "Confirmation gates and serial redaction hold on the real board",
           "run_uno.py results", i5)

    def i6():
        boards = enumerate_boards()
        return PASS, {"inventory": [{"port": b["port"], "name": b.get("friendly_name"),
                                     "type": b.get("board_type")} for b in boards]}
    ev.run("NIS2-I-6", "21(2)(i)", "Connected hardware assets are inventoried", "boards.enumerate_boards", i6)

    ev.run("NIS2-I-7", "21(2)(i)", "Human-resources security", "n/a",
           lambda: (NA, "Organisational measure; a desktop plugin has no staff processes."))

    # ------------------------------------------------------------------ (j) authentication and secured comms
    def j1():
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.toml"
            path.write_text('[[ming.mqtt]]\nname="a"\nhost="broker.lan"\nsecurity={tls=false}\n', encoding="utf-8")
            path.chmod(0o600)
            real, config.CONFIG_PATH = config.CONFIG_PATH, path
            try:
                config.load()
                outcome = "accepted"
            except config.ConfigError as exc:
                outcome = f"refused: {exc}"[:160]
            finally:
                config.CONFIG_PATH = real
        return (PASS if outcome.startswith("refused") else FAIL), outcome
    ev.run("NIS2-J-1", "21(2)(j)", "Cleartext MQTT to a remote host needs an explicit waiver", "config.load", j1)

    def j2():
        user = gh_json("user")
        enabled = user.get("two_factor_authentication")
        if enabled is None:
            return LIMIT, ("GitHub reports 2FA only to a token with the user scope; this token has repo, workflow, "
                           "read:org and gist. Confirm under GitHub Settings -> Password and authentication.")
        return (PASS if enabled else FAIL), {"maintainer_github_2fa": enabled}
    ev.run("NIS2-J-2", "21(2)(j)", "The maintainer's GitHub account uses 2FA", "GitHub API", j2)

    ev.run("NIS2-J-3", "21(2)(j)", "MFA for the local MCP server", "n/a",
           lambda: (NA, "Runs as the logged-in user over stdio; there is no network login to protect."))

    counts = {s: sum(c["status"] == s for c in ev.checks) for s in (PASS, FAIL, NA, LIMIT)}
    result = {
        "scope": "Technical evidence for NIS2 Article 21(2) measures and Article 23 support. Not a certification: "
                 "NIS2 obliges organisations; the product-level EU law is the Cyber Resilience Act.",
        "plugin_commit": sh("git", "rev-parse", "HEAD", cwd=plugin).stdout.strip(),
        "release": args.release,
        "date": time.strftime("%Y-%m-%d"),
        "summary": counts,
        "checks": ev.checks,
    }
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"{counts}", file=sys.stderr)
    sys.exit(1 if counts[FAIL] else 0)


if __name__ == "__main__":
    main()
