"""Exercise the validation runner without real credentials, Docker or hardware."""

import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from hardware_validation import run_ming
from omarchy_hardware import mqtt_lite

ROOT = Path(__file__).resolve().parents[2]
INFLUX_FIXTURE = "public-influx-test-marker"
NODERED_FIXTURE = "public-nodered-test-marker"


@pytest.fixture
def runner(monkeypatch, tmp_path):
    directory = tmp_path / "secrets"
    directory.mkdir()
    for name, value in (("influxdb-admin-token", INFLUX_FIXTURE), ("nodered-token", NODERED_FIXTURE),
                        ("mqtt-device-password", "public-device-test-marker")):
        (directory / name).write_text(value)
    monkeypatch.setattr(run_ming.time, "sleep", lambda _: None)
    monkeypatch.setattr(run_ming.time, "time_ns", lambda: 123)

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def publish(self, *args, **kwargs):
            pass

    monkeypatch.setattr(mqtt_lite, "Client", Client)
    return tmp_path


def run_end_to_end(monkeypatch, runner, query_output="\n200", query_exit=0, api_exit=0):
    calls = []

    def shell(*args, **kwargs):
        calls.append((args, kwargs))
        if "/api/v2/query" in args[-1]:
            return subprocess.CompletedProcess(args, query_exit, query_output, "")
        return subprocess.CompletedProcess(args, api_exit, "401", "")

    monkeypatch.setattr(run_ming, "sh", shell)
    checks = run_ming.Checks()
    run_ming.end_to_end(checks, runner, ROOT)
    return checks.items, calls


def test_both_authorization_headers_use_stdin(monkeypatch, runner):
    checks, calls = run_end_to_end(monkeypatch, runner)
    assert all(c["status"] == "pass" for c in checks)
    for args, kwargs in calls:
        assert INFLUX_FIXTURE not in repr(args)
        assert NODERED_FIXTURE not in repr(args)
        assert args[:2] == ("curl", "-q")  # do not load an ambient curlrc
        if "/api/v2/query" in args[-1]:
            assert kwargs["stdin"] == f"Authorization: Token {INFLUX_FIXTURE}\n"
            assert args[args.index("-H") + 1] == "@-"
            assert args[args.index("--data-binary") + 1].startswith('from(bucket: "sensors")')
        elif "-X" in args:
            assert kwargs["stdin"] == f"Authorization: Bearer {NODERED_FIXTURE}\n"
            assert args[args.index("-H") + 1] == "@-"
    evidence = json.dumps(checks)
    assert INFLUX_FIXTURE not in evidence and NODERED_FIXTURE not in evidence


@pytest.mark.parametrize(("output", "exit_code"), [
    ("", 7), ("\n000", 60), ("\n200", 28), ('{"error":"unauthorized"}\n401', 0),
    ("\n302", 0), ("<html>error</html>\n200", 0), ("error,reference\nbad query,123\n\n200", 0),
    (",result,table,_measurement\n,too,few\n\n200", 0), ("#datatype,string\n\n200", 0),
])
def test_query_failure_or_invalid_evidence_is_not_a_pass(monkeypatch, runner, output, exit_code):
    checks, _ = run_end_to_end(monkeypatch, runner, output, exit_code)
    assert checks[0]["status"] == "fail"
    assert checks[0]["evidence"]["extra_measurement_stored"] is None


def test_injected_measurement_is_detected(monkeypatch, runner):
    checks, _ = run_end_to_end(monkeypatch, runner, ",result,table,_measurement\n,_result,0,injected123\n\n200")
    assert checks[0]["status"] == "fail"
    assert checks[0]["evidence"]["extra_measurement_stored"] is True


def test_api_probe_must_finish_successfully(monkeypatch, runner):
    checks, _ = run_end_to_end(monkeypatch, runner, api_exit=28)
    assert checks[1]["status"] == "fail"


@pytest.mark.parametrize("value", ["", "fixture\rheader", "fixture\nheader", "fixture\x00header"])
def test_header_injection_refused_without_disclosing_value(value):
    with pytest.raises(ValueError, match="^Invalid authorization header$"):
        run_ming.authorization_header("Token", value)


@pytest.fixture
def infrastructure(monkeypatch, tmp_path):
    for folder in ("certs", "secrets"):
        (tmp_path / folder).mkdir(mode=0o700)
    images = {s: "fixture:tag@sha256:" + str(i) * 64 for i, s in enumerate(run_ming.SERVICES)}
    rows = [{"Service": s, "Name": s, "Image": images[s], "State": "running",
             "Publishers": [{"PublishedPort": 123, "URL": "127.0.0.1"}]} for s in run_ming.SERVICES]

    def shell(*args, **kwargs):
        if args[0] == "openssl":
            if "-tls1_1" in args:
                return subprocess.CompletedProcess(args, 1, "", "tlsv1 alert protocol version")
            version = "TLSv1.2" if "-tls1_2" in args else "TLSv1.3"
            output = f"New, {version}, Cipher is TEST\nVerify return code: 0 (ok)"
            return subprocess.CompletedProcess(args, 0, output, "")
        if "ps" in args:
            output = "\n".join(json.dumps(row) for row in rows)
        elif "config" in args:
            output = json.dumps({"services": {s: {"image": image} for s, image in images.items()}})
        elif "{{.Image}}" in args:
            output = args[-1]
        elif "{{json .RepoDigests}}" in args:
            output = json.dumps([images[args[-1]].replace(":tag", "")])
        elif "inspect" in args:
            output = "PATH=/usr/bin\n"
        else:
            output = ""
        return subprocess.CompletedProcess(args, 0, output, "")

    monkeypatch.setattr(run_ming, "sh", shell)
    return tmp_path, shell, rows, images


def test_complete_infrastructure_evidence_passes(infrastructure):
    stack, _, _, _ = infrastructure
    checks = run_ming.Checks()
    run_ming.infrastructure(checks, stack)
    assert len(checks.items) == 7
    assert all(c["status"] == "pass" for c in checks.items)


@pytest.mark.parametrize(("failure", "check_id"), [
    ("ps", "MING-I-1"), ("ps", "MING-I-3"), ("ps", "MING-I-6"),
    ("exec", "MING-I-5"), ("inspect", "MING-I-6"), ("openssl", "MING-I-4"),
    ("config", "MING-I-3"),
])
def test_failed_infrastructure_probe_is_not_a_pass(monkeypatch, infrastructure, failure, check_id):
    stack, shell, _, _ = infrastructure

    def failed(*args, **kwargs):
        if failure in args:
            return subprocess.CompletedProcess(args, 1, "", "public failure fixture")
        return shell(*args, **kwargs)

    monkeypatch.setattr(run_ming, "sh", failed)
    checks = run_ming.Checks()
    run_ming.infrastructure(checks, stack)
    assert next(c for c in checks.items if c["id"] == check_id)["status"] == "fail"


def test_wrong_service_image_is_not_accepted(infrastructure):
    stack, _, rows, images = infrastructure
    rows[0]["Name"] = "influxdb"  # a different, valid digest from the same compose file
    checks = run_ming.Checks()
    run_ming.infrastructure(checks, stack)
    assert checks.items[2]["status"] == "fail"


def test_native_curl_stdin_header_body_and_process_visibility(tmp_path):
    curl = shutil.which("curl")
    assert curl, "curl is required for this credential transport regression"
    received = {}
    arrived, release = threading.Event(), threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received["headers"] = dict(self.headers)
            received["body"] = self.rfile.read(int(self.headers["Content-Length"]))
            arrived.set()
            release.wait(5)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    (tmp_path / ".curlrc").write_text('header = "X-Ambient: unwanted"\n')
    env = dict(os.environ, CURL_HOME=str(tmp_path))
    body = 'from(bucket: "public-test")'
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as httpd:
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with subprocess.Popen([curl, "-q", "--noproxy", "*", "-sS", "-H", "@-", "--data-binary", body,  # noqa: S603 - local curl, public fixtures
                                   f"http://127.0.0.1:{httpd.server_port}/"],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env=env) as process:
                try:
                    process.stdin.write(run_ming.authorization_header("Token", INFLUX_FIXTURE).encode())
                    process.stdin.close()
                    process.stdin = None
                    assert arrived.wait(5), "curl did not deliver the request"
                    if sys.platform.startswith("linux"):
                        # Mandatory on Linux, not skipped when /proc cannot be read.
                        for name in ("cmdline", "environ"):
                            assert INFLUX_FIXTURE.encode() not in Path(f"/proc/{process.pid}/{name}").read_bytes()
                    assert INFLUX_FIXTURE not in repr(process.args)
                    assert received["headers"]["Authorization"] == f"Token {INFLUX_FIXTURE}"
                    assert "X-Ambient" not in received["headers"]
                    assert received["body"] == body.encode()
                finally:
                    release.set()
                    stdout, stderr = process.communicate(timeout=5)
                assert process.returncode == 0, stderr
                assert stdout == b"ok"
        finally:
            release.set()
            httpd.shutdown()
            thread.join(timeout=3)
