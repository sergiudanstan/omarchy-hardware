"""MING stack: query and line-protocol builders, secrets, policy, HTTP client and the tools end to end."""

import json
import os

import pytest
from ming_fakes import FakeBroker, FakeHttp, json_route, wait_for

from omarchy_hardware import audit, http_lite, ming, policy, server
from omarchy_hardware.config import (
    Config,
    HttpSecurity,
    MingGrafana,
    MingInfluxDb,
    MingMqttBroker,
    MingNodeRed,
    MqttSecurity,
)
from omarchy_hardware.errors import (
    HOST_NOT_ALLOWED,
    INSECURE_TRANSPORT,
    INVALID_ARGUMENT,
    RATE_LIMITED,
    SERVICE_ERROR,
    UNCONFIRMED,
    WRITE_TOO_LARGE,
    ToolError,
)

PLAIN = MqttSecurity(tls=False)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(audit, "STATE_DIR", tmp_path / "state")
    audit._chain.reset()
    monkeypatch.setattr(server, "_ming_budget", None)


def _use(monkeypatch, **fields):
    config = Config(ming_allow=True, **fields)
    monkeypatch.setattr(server, "_config", lambda: config)
    return config


def _audit_events():
    path = audit.log_path()
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


# --------------------------------------------------------------------------- Flux


def test_flux_string_escapes_quotes_backslashes_and_interpolation():
    assert ming.flux_string('a"b') == '"a\\"b"'
    assert ming.flux_string("a\\b") == '"a\\\\b"'
    assert ming.flux_string("${secrets.get(key: \"x\")}") == '"\\${secrets.get(key: \\"x\\")}"'


@pytest.mark.parametrize("value", ["-1h", "-30m", "-1h30m", "-2w", "now", "2026-09-18T10:00:00Z", "2026-09-18T10:00:00.5+02:00"])
def test_flux_time_accepts(value):
    assert ming.flux_time(value, "start")


@pytest.mark.parametrize("value", ["1h", "-1x", "yesterday", "-1h) |> to(bucket: \"x\"", "2026-09-18", ""])
def test_flux_time_rejects(value):
    with pytest.raises(ToolError) as caught:
        ming.flux_time(value, "start")
    assert caught.value.code == INVALID_ARGUMENT


def test_build_query_is_fully_parameterised():
    query = ming.build_query(
        "sensors",
        'temp") |> to(bucket: "other',
        field="value",
        tags={"room": "lab"},
        start="-6h",
        aggregate="mean",
        every="5m",
        limit=50,
    )
    assert query.startswith('from(bucket: "sensors")')
    assert 'r._measurement == "temp\\") |> to(bucket: \\"other"' in query
    assert 'r["room"] == "lab"' in query
    assert "aggregateWindow(every: 5m, fn: mean, createEmpty: false)" in query
    assert query.endswith("limit(n: 50)")
    # The injection attempt stayed inside a string literal: exactly one pipeline stage per line.
    assert query.count("|> to(") == 1 and '\\"other' in query


@pytest.mark.parametrize(
    "kwargs",
    [
        {"aggregate": "mean"},
        {"aggregate": "stddev", "every": "1m"},
        {"every": "1m"},
        {"aggregate": "mean", "every": "1m; drop()"},
        {"limit": 0},
        {"limit": 5000},
        {"tags": {"k": "line\nbreak"}},
        {"field": ""},
    ],
)
def test_build_query_rejects_bad_parameters(kwargs):
    with pytest.raises(ToolError) as caught:
        ming.build_query("b", "m", **kwargs)
    assert caught.value.code == INVALID_ARGUMENT


def test_parse_csv_handles_multiple_tables_and_errors():
    text = ",result,table,_value\r\n,_result,0,a\r\n,_result,0,b\r\n\r\n,result,table,_value,kind\r\n,_result,1,c,tag\r\n"
    rows = ming.parse_csv(text, 10)
    assert [row["_value"] for row in rows] == ["a", "b", "c"]
    assert rows[2]["kind"] == "tag"
    assert len(ming.parse_csv(text, 2)) == 2
    with pytest.raises(http_lite.HttpError, match="the query failed: bad thing"):
        ming.parse_csv("error,reference\r\nbad   thing,\r\n", 10)


# --------------------------------------------------------------------------- line protocol


def test_build_line_escapes_every_element():
    line = ming.build_line(
        "room temp,x",
        {"value": 21.5, "count": 3, "ok": True, "note": 'say "hi" \\ bye'},
        {"room=a": "lab 1,2"},
        1_700_000_000,
    ).decode()
    assert line == (
        'room\\ temp\\,x,room\\=a=lab\\ 1\\,2 count=3i,note="say \\"hi\\" \\\\ bye",ok=true,value=21.5 1700000000'
    )


@pytest.mark.parametrize(
    ("measurement", "fields", "tags"),
    [
        ("m", {"v": "two\nlines"}, None),
        ("m\nother v=1", {"v": 1.0}, None),
        ("m", {"v": float("nan")}, None),
        ("m", {"v": [1, 2]}, None),
        ("m", {}, None),
        ("_internal", {"v": 1.0}, None),
        ("m", {"_field": 1.0}, None),
        ("m", {"v": 1.0}, {"t": ""}),
        ("m", {"v": 2**63}, None),
    ],
)
def test_build_line_rejects_what_could_break_the_point(measurement, fields, tags):
    with pytest.raises(ToolError) as caught:
        ming.build_line(measurement, fields, tags)
    assert caught.value.code == INVALID_ARGUMENT


# --------------------------------------------------------------------------- secrets


def test_secret_from_environment(monkeypatch):
    monkeypatch.setenv("MING_TOKEN", "abc")
    assert ming.read_secret("MING_TOKEN", None, "x") == "abc"
    monkeypatch.delenv("MING_TOKEN")
    with pytest.raises(ToolError, match="MING_TOKEN is not set"):
        ming.read_secret("MING_TOKEN", None, "x")


def test_secret_file_must_be_private_and_not_a_symlink(tmp_path):
    secret = tmp_path / "token"
    secret.write_text("abc\n")
    os.chmod(secret, 0o600)
    assert ming.read_secret(None, str(secret), "x") == "abc"

    os.chmod(secret, 0o640)
    with pytest.raises(ToolError, match="readable by other users"):
        ming.read_secret(None, str(secret), "x")

    os.chmod(secret, 0o600)
    link = tmp_path / "link"
    link.symlink_to(secret)
    with pytest.raises(ToolError, match="cannot open"):
        ming.read_secret(None, str(link), "x")

    secret.write_text("one\ntwo\n")
    with pytest.raises(ToolError, match="one non-empty line"):
        ming.read_secret(None, str(secret), "x")


# --------------------------------------------------------------------------- policy


def test_disabled_stack_refuses_everything(monkeypatch):
    monkeypatch.setattr(
        server, "_config", lambda: Config(ming_mqtt=(MingMqttBroker("local", "127.0.0.1", 1883, security=PLAIN),))
    )
    for result in (server.ming_status(), server.mqtt_subscribe("a"), server.nodered_flows()):
        assert result["error"]["code"] == HOST_NOT_ALLOWED
        assert "disabled" in result["error"]["message"]


def test_target_names_are_required_when_ambiguous():
    config = Config(
        ming_allow=True,
        ming_mqtt=(
            MingMqttBroker("a", "127.0.0.1", 1883, security=PLAIN),
            MingMqttBroker("b", "127.0.0.1", 1884, security=PLAIN),
        ),
    )
    with pytest.raises(ToolError, match="name the one you mean") as caught:
        policy.ming_mqtt(config, None)
    assert "a, b" in caught.value.hint
    assert policy.ming_mqtt(config, "b").port == 1884
    with pytest.raises(ToolError, match="No mqtt target is named 'c'"):
        policy.ming_mqtt(config, "c")


def test_cleartext_to_a_remote_host_is_refused_even_if_config_was_bypassed():
    remote = Config(ming_allow=True, ming_mqtt=(MingMqttBroker("r", "broker.lan", 1883, security=PLAIN),))
    with pytest.raises(ToolError) as caught:
        policy.ming_mqtt(remote, None)
    assert caught.value.code == INSECURE_TRANSPORT

    http = Config(ming_allow=True, ming_grafana=(MingGrafana("g", "http://grafana.lan"),))
    with pytest.raises(ToolError) as caught:
        policy.ming_grafana(http, None)
    assert caught.value.code == INSECURE_TRANSPORT

    for url in ("http://localhost:3000", "http://127.0.0.1", "http://[::1]", "https://grafana.lan"):
        assert policy.ming_grafana(Config(ming_allow=True, ming_grafana=(MingGrafana("g", url),)), None)


def test_allowlists():
    broker = MingMqttBroker("m", "127.0.0.1", 1883, subscribe=("plant/#",), publish=("plant/fan",), security=PLAIN)
    assert policy.check_ming_subscribe(broker, "plant/line1/+")
    for denied in ("#", "plant2/x", "$SYS/#"):
        with pytest.raises(ToolError):
            policy.check_ming_subscribe(broker, denied)
    assert policy.check_ming_publish(broker, "plant/fan")
    with pytest.raises(ToolError):
        policy.check_ming_publish(broker, "plant/+")

    db = MingInfluxDb("i", "http://localhost:8086", "o", read_buckets=("r",), write_buckets=("w",))
    assert policy.check_ming_bucket(db, "r", write=False)
    with pytest.raises(ToolError):
        policy.check_ming_bucket(db, "r", write=True)

    nodered = MingNodeRed("n", "http://localhost:1880", inject_nodes=("abc",))
    assert policy.check_ming_inject(nodered, "abc")
    with pytest.raises(ToolError):
        policy.check_ming_inject(nodered, "abd")

    config = Config(ming_allow=True, ming_grafana=(MingGrafana("g", "http://localhost:3000"),))
    with pytest.raises(ToolError, match="Annotations are not enabled"):
        policy.ming_grafana(config, None, annotate=True)


# --------------------------------------------------------------------------- HTTP client


@pytest.fixture
def http_factory():
    servers = []

    def make(routes):
        fake = FakeHttp(routes)
        servers.append(fake)
        return fake

    yield make
    for fake in servers:
        fake.close()


def test_http_does_not_follow_redirects(http_factory):
    target = http_factory({("GET", "/ok"): json_route(200, {"leaked": True})})
    fake = http_factory({("GET", "/api/health"): (302, {"Location": f"{target.url}/ok"}, b"")})
    with pytest.raises(http_lite.HttpError, match="redirects are not followed"):
        http_lite.request("GET", f"{fake.url}/api/health", timeout=2, headers={"Authorization": "Bearer t"})
    assert target.requests == []


def test_http_ignores_proxy_environment(http_factory, monkeypatch):
    fake = http_factory({("GET", "/health"): json_route(200, {"status": "pass"})})
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    assert http_lite.request("GET", f"{fake.url}/health", timeout=2).json() == {"status": "pass"}


def test_http_caps_response_size(http_factory):
    fake = http_factory({("GET", "/big"): (200, {}, b"x" * 2048)})
    with pytest.raises(http_lite.HttpError, match="exceeded 1024 bytes"):
        http_lite.request("GET", f"{fake.url}/big", timeout=2, max_bytes=1024)


def test_http_error_detail_is_short_and_single_line(http_factory):
    fake = http_factory({("GET", "/x"): json_route(401, {"message": "unauthorized\naccess " + "y" * 500})})
    with pytest.raises(http_lite.HttpError) as caught:
        http_lite.request("GET", f"{fake.url}/x", timeout=2)
    assert caught.value.status == 401
    assert "\n" not in str(caught.value) and len(str(caught.value)) < 260


# --------------------------------------------------------------------------- tools, end to end


@pytest.fixture
def broker():
    fake = FakeBroker(retained=(("plant/line1/temp", b"21.5"),), deliver=(("plant/line1/humidity", b"40"),))
    yield fake
    fake.close()


def test_mqtt_tools_end_to_end(monkeypatch, broker):
    _use(
        monkeypatch,
        ming_mqtt=(
            MingMqttBroker(
                "local", "127.0.0.1", broker.port, subscribe=("plant/#",), publish=("plant/fan",), security=PLAIN
            ),
        ),
    )

    read = server.mqtt_subscribe("plant/line1/+", seconds=1)
    assert read["ok"] is True, read
    assert [(m["topic"], m["payload"], m["retained"]) for m in read["messages"]] == [
        ("plant/line1/temp", "21.5", True),
        ("plant/line1/humidity", "40", False),
    ]
    assert "127.0.0.1" not in json.dumps(read)

    assert server.mqtt_subscribe("#")["error"]["code"] == HOST_NOT_ALLOWED
    assert server.mqtt_subscribe("plant/#/x")["error"]["code"] == INVALID_ARGUMENT

    assert server.mqtt_publish("plant/fan", "on")["error"]["code"] == UNCONFIRMED
    assert server.mqtt_publish("plant/other", "on", confirm=True)["error"]["code"] == HOST_NOT_ALLOWED
    sent = server.mqtt_publish("plant/fan", "0a0b", encoding="hex", qos=1, confirm=True)
    assert sent["ok"] is True, sent
    assert wait_for(lambda: broker.published)
    assert broker.published[0] == {"topic": "plant/fan", "payload": b"\n\x0b", "qos": 1, "retain": False}

    events = _audit_events()
    assert [e["event"] for e in events] == ["mqtt_publish"]
    assert events[0]["bytes"] == 2 and events[0]["topic"] == "plant/fan" and "payload" not in events[0]
    assert audit.verify()["ok"] is True


def test_mqtt_publish_limits(monkeypatch, broker):
    _use(
        monkeypatch,
        ming_max_payload_bytes=4,
        ming_write_budget_per_min=2,
        ming_mqtt=(MingMqttBroker("local", "127.0.0.1", broker.port, publish=("t",), security=PLAIN),),
    )
    assert server.mqtt_publish("t", "12345", confirm=True)["error"]["code"] == WRITE_TOO_LARGE
    assert server.mqtt_publish("t", "1", qos=2, confirm=True)["error"]["code"] == INVALID_ARGUMENT
    assert server.mqtt_publish("t", "1", confirm=True)["ok"] is True
    assert server.mqtt_publish("t", "2", confirm=True)["ok"] is True
    assert server.mqtt_publish("t", "3", confirm=True)["error"]["code"] == RATE_LIMITED


def test_subscribe_omits_payloads_over_the_configured_cap(monkeypatch):
    fake = FakeBroker(deliver=(("t/small", b"ok"), ("t/big", b"x" * 100)))
    try:
        _use(
            monkeypatch,
            ming_max_payload_bytes=10,
            ming_mqtt=(MingMqttBroker("local", "127.0.0.1", fake.port, subscribe=("t/#",), security=PLAIN),),
        )
        messages = server.mqtt_subscribe("t/#", seconds=1)["messages"]
        assert messages[0]["payload"] == "ok"
        assert messages[1]["bytes"] == 100 and "payload" not in messages[1] and "omitted" in messages[1]
    finally:
        fake.close()


def test_publish_is_refused_when_the_audit_log_is_unwritable(monkeypatch, broker, tmp_path):
    _use(monkeypatch, ming_mqtt=(MingMqttBroker("local", "127.0.0.1", broker.port, publish=("t",), security=PLAIN),))
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    monkeypatch.setattr(audit, "STATE_DIR", blocker / "state")
    result = server.mqtt_publish("t", "1", confirm=True)
    assert result["error"]["code"] == "AUDIT_LOG_FAILED"
    assert broker.published == []


# As InfluxDB 2.9 sends it with the datatype annotation: a whole float prints as "21".
INFLUX_CSV = (
    b"#datatype,string,long,dateTime:RFC3339,dateTime:RFC3339,dateTime:RFC3339,double,string,string,string\r\n"
    b",result,table,_start,_stop,_time,_value,_field,_measurement,room\r\n"
    b",_result,0,x,y,2026-09-18T10:00:00Z,21.5,value,temp,lab\r\n"
    b",_result,0,x,y,2026-09-18T10:01:00Z,NaN,value,temp,lab\r\n"
    b",_result,0,x,y,2026-09-18T10:02:00Z,21,value,temp,lab\r\n"
    b"\r\n"
    b"#datatype,string,long,dateTime:RFC3339,dateTime:RFC3339,dateTime:RFC3339,long,string,string,string\r\n"
    b",result,table,_start,_stop,_time,_value,_field,_measurement,room\r\n"
    b",_result,1,x,y,2026-09-18T10:00:00Z,40,humidity,temp,lab\r\n"
)


def test_influx_tools_end_to_end(monkeypatch, http_factory, tmp_path):
    token = tmp_path / "influx-token"
    token.write_text("tok\n")
    os.chmod(token, 0o600)
    fake = http_factory(
        {
            ("POST", "/influx/api/v2/query"): (200, {"Content-Type": "text/csv"}, INFLUX_CSV),
            ("POST", "/influx/api/v2/write"): (204, {}, b""),
        }
    )
    _use(
        monkeypatch,
        ming_influxdb=(
            MingInfluxDb(
                "local",
                f"{fake.url}/influx",
                "home lab",
                read_buckets=("sensors",),
                write_buckets=("claude",),
                security=HttpSecurity(token_file=str(token)),
            ),
        ),
    )

    result = server.influx_query("sensors", "temp", field="value", tags={"room": "lab"}, start="-2h")
    assert result["ok"] is True, result
    assert result["points"][0] == {
        "time": "2026-09-18T10:00:00Z",
        "measurement": "temp",
        "field": "value",
        "value": 21.5,
        "tags": {"room": "lab"},
    }
    assert result["points"][1]["value"] == "NaN"
    # The annotation keeps a whole float a float, and an integer field an integer.
    assert result["points"][2]["value"] == 21.0 and isinstance(result["points"][2]["value"], float)
    assert result["points"][3]["value"] == 40 and isinstance(result["points"][3]["value"], int)
    assert "#datatype" not in json.dumps(result["points"])
    request = fake.requests[-1]
    assert request["headers"]["authorization"] == "Token tok"
    assert request["query"] == "org=home+lab"
    body = json.loads(request["body"])
    assert body["type"] == "flux" and 'from(bucket: "sensors")' in body["query"]
    assert body["dialect"]["annotations"] == ["datatype"]

    assert server.influx_query("secret", "temp")["error"]["code"] == HOST_NOT_ALLOWED
    assert server.influx_write("sensors", "m", {"v": 1.0}, confirm=True)["error"]["code"] == HOST_NOT_ALLOWED
    assert server.influx_write("claude", "m", {"v": 1.0})["error"]["code"] == UNCONFIRMED

    written = server.influx_write("claude", "setpoint", {"value": 21.0}, tags={"room": "lab"}, confirm=True)
    assert written["ok"] is True, written
    request = fake.requests[-1]
    assert request["body"] == b"setpoint,room=lab value=21.0"
    assert "bucket=claude" in request["query"] and "precision=s" in request["query"]
    assert [e["event"] for e in _audit_events()] == ["influx_write"]


def test_influx_measurements(monkeypatch, http_factory):
    csv_body = b",result,table,_value\r\n,_result,0,temp\r\n,_result,0,humidity\r\n"
    fake = http_factory({("POST", "/api/v2/query"): (200, {}, csv_body)})
    _use(monkeypatch, ming_influxdb=(MingInfluxDb("local", fake.url, "o", read_buckets=("sensors",)),))
    result = server.influx_measurements("sensors")
    assert result["measurements"] == ["humidity", "temp"]
    assert "schema.measurements" in json.loads(fake.requests[-1]["body"])["query"]


def test_service_errors_are_reported_with_a_hint(monkeypatch, http_factory):
    fake = http_factory({("POST", "/api/v2/query"): json_route(401, {"message": "unauthorized access"})})
    _use(monkeypatch, ming_influxdb=(MingInfluxDb("local", fake.url, "o", read_buckets=("b",)),))
    result = server.influx_query("b", "m")
    assert result["error"]["code"] == SERVICE_ERROR
    assert "HTTP 401: unauthorized access" in result["error"]["message"]
    assert "token" in result["error"]["hint"]
    assert fake.url not in json.dumps(result)


FLOWS = [
    {"id": "t1", "type": "tab", "label": "Greenhouse"},
    {"id": "i1", "type": "inject", "z": "t1", "name": "Vent open"},
    {"id": "i2", "type": "inject", "z": "t1", "name": ""},
    {"id": "f1", "type": "function", "z": "t1", "func": "require('child_process').exec('rm -rf /')"},
    {"id": "m1", "type": "mqtt out", "z": "t1"},
]


def test_nodered_tools_end_to_end(monkeypatch, http_factory):
    fake = http_factory(
        {
            ("GET", "/flows"): json_route(200, FLOWS),
            ("POST", "/inject/i1"): (200, {}, b"OK"),
            ("GET", "/settings"): json_route(200, {"version": "5.0.7"}),
        }
    )
    _use(monkeypatch, ming_nodered=(MingNodeRed("local", fake.url, inject_nodes=("i1",)),))

    flows = server.nodered_flows()
    assert flows["ok"] is True, flows
    assert flows["flows"] == [{"id": "t1", "label": "Greenhouse", "disabled": False}]
    assert flows["node_counts"] == {"function": 1, "inject": 2, "mqtt out": 1}
    assert flows["inject_nodes"][0] == {"id": "i1", "name": "Vent open", "flow": "Greenhouse", "allowlisted": True}
    assert flows["inject_nodes"][1]["allowlisted"] is False
    assert "child_process" not in json.dumps(flows)
    assert fake.requests[-1]["headers"]["node-red-api-version"] == "v1"

    assert server.nodered_inject("i2", confirm=True)["error"]["code"] == HOST_NOT_ALLOWED
    assert server.nodered_inject("i1")["error"]["code"] == UNCONFIRMED
    assert server.nodered_inject("i1", confirm=True)["ok"] is True
    assert fake.requests[-1]["method"] == "POST" and fake.requests[-1]["path"] == "/inject/i1"
    assert [e["event"] for e in _audit_events()] == ["nodered_inject"]


def test_grafana_tools_end_to_end(monkeypatch, http_factory):
    monkeypatch.setenv("GRAFANA_TOKEN", "glsa_x")
    fake = http_factory(
        {
            ("GET", "/api/search"): json_route(
                200, [{"uid": "u1", "title": "Plant", "folderTitle": "Ops", "tags": ["iot"], "url": "/d/u1"}]
            ),
            ("POST", "/api/annotations"): json_route(200, {"id": 7, "message": "Annotation added"}),
        }
    )
    _use(
        monkeypatch,
        ming_grafana=(MingGrafana("local", fake.url, annotate=True, security=HttpSecurity(token_env="GRAFANA_TOKEN")),),
    )

    boards = server.grafana_dashboards(query="Plant", limit=5)
    assert boards["dashboards"] == [{"uid": "u1", "title": "Plant", "folder": "Ops", "tags": ["iot"]}]
    assert fake.requests[-1]["headers"]["authorization"] == "Bearer glsa_x"
    assert "type=dash-db" in fake.requests[-1]["query"] and "query=Plant" in fake.requests[-1]["query"]

    assert server.grafana_annotate("Fan on")["error"]["code"] == UNCONFIRMED
    assert server.grafana_annotate("x", panel_id=2, confirm=True)["error"]["code"] == INVALID_ARGUMENT
    noted = server.grafana_annotate("Fan on", tags=["claude"], dashboard_uid="u1", panel_id=2, confirm=True)
    assert noted == {"ok": True, "grafana": "local", "annotation_id": 7}
    assert json.loads(fake.requests[-1]["body"]) == {
        "text": "Fan on",
        "tags": ["claude"],
        "dashboardUID": "u1",
        "panelId": 2,
    }


def test_ming_status_probes_in_parallel_and_hides_addresses(monkeypatch, http_factory, broker):
    influx = http_factory({("GET", "/health"): json_route(200, {"status": "pass", "version": "v2.9.1"})})
    grafana = http_factory({("GET", "/api/health"): json_route(200, {"database": "ok", "version": "13.2.2"})})
    _use(
        monkeypatch,
        ming_mqtt=(MingMqttBroker("mq", "127.0.0.1", broker.port, subscribe=("a/#",), security=PLAIN),),
        ming_influxdb=(MingInfluxDb("db", influx.url, "o"),),
        ming_nodered=(MingNodeRed("nr", "http://127.0.0.1:9"),),
        ming_grafana=(MingGrafana("gf", grafana.url),),
    )
    status = server.ming_status()
    assert status["ok"] is True, status
    assert status["mqtt"][0] == {"name": "mq", "tls": False, "subscribe": ["a/#"], "publish": [], "reachable": True}
    assert status["influxdb"][0]["version"] == "v2.9.1"
    assert status["nodered"][0]["reachable"] is False
    assert status["grafana"][0]["version"] == "13.2.2"
    assert "127.0.0.1" not in json.dumps(status)


# --------------------------------------------------------------------------- TLS


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    import shutil

    from ming_fakes import make_pki

    if shutil.which("openssl") is None:
        pytest.skip("openssl is needed to issue test certificates")
    return make_pki(tmp_path_factory.mktemp("pki"))


def test_https_verifies_against_the_configured_ca(monkeypatch, pki):
    ca_file, context = pki
    fake = FakeHttp({("GET", "/api/health"): json_route(200, {"database": "ok", "version": "13.2.2"})}, tls=context)
    try:
        _use(monkeypatch, ming_grafana=(MingGrafana("tls", fake.url, security=HttpSecurity(ca_file=str(ca_file))),))
        assert server.ming_status()["grafana"][0] == {
            "name": "tls", "annotate": False, "reachable": True, "database": "ok", "version": "13.2.2"
        }

        # Without the private CA, the system trust store must reject the certificate.
        _use(monkeypatch, ming_grafana=(MingGrafana("tls", fake.url),))
        probe = server.ming_status()["grafana"][0]
        assert probe["reachable"] is False and "TLS failed" in probe["error"]
    finally:
        fake.close()


def test_https_rejects_a_certificate_for_another_name(pki, tmp_path):
    from ming_fakes import make_pki

    ca_file, context = make_pki(tmp_path, san="DNS:grafana.elsewhere.test")
    fake = FakeHttp({("GET", "/x"): json_route(200, {})}, tls=context)
    try:
        with pytest.raises(http_lite.HttpError, match="TLS failed"):
            http_lite.request("GET", f"{fake.url}/x", timeout=2, ca_file=str(ca_file))
    finally:
        fake.close()


def test_mqtt_over_tls(monkeypatch, pki):
    ca_file, context = pki
    fake = FakeBroker(tls=context, retained=(("sensors/t", b"20"),))
    try:
        secure = MqttSecurity(tls=True, ca_file=str(ca_file))
        _use(monkeypatch, ming_mqtt=(MingMqttBroker("tls", "localhost", fake.port, subscribe=("sensors/#",),
                                                    publish=("actuators/fan",), security=secure),))
        read = server.mqtt_subscribe("sensors/#", seconds=1)
        assert read["ok"] is True, read
        assert read["messages"][0]["payload"] == "20"
        assert server.mqtt_publish("actuators/fan", "on", qos=1, confirm=True)["ok"] is True

        _use(monkeypatch, ming_mqtt=(MingMqttBroker("tls", "localhost", fake.port, subscribe=("sensors/#",),
                                                    security=MqttSecurity(tls=True)),))
        refused = server.mqtt_subscribe("sensors/#", seconds=1)
        assert refused["error"]["code"] == SERVICE_ERROR
        assert "TLS handshake failed" in refused["error"]["message"]
    finally:
        fake.close()


def test_a_missing_ca_file_is_named_not_hidden(monkeypatch, tmp_path):
    missing = str(tmp_path / "nope.pem")
    _use(
        monkeypatch,
        ming_grafana=(MingGrafana("g", "https://127.0.0.1:9", security=HttpSecurity(ca_file=missing)),),
        ming_mqtt=(MingMqttBroker("m", "127.0.0.1", 9, subscribe=("a",), security=MqttSecurity(ca_file=missing)),),
    )
    grafana = server.grafana_dashboards()
    assert grafana["error"]["code"] == "SERVICE_UNREACHABLE"
    assert "cannot load the configured ca_file" in grafana["error"]["message"]
    mqtt = server.mqtt_subscribe("a", seconds=1)
    assert "cannot load the configured TLS files" in mqtt["error"]["message"]
