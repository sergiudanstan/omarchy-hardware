import json
import os
import pty
import time

import pytest

from omarchy_hardware import audit, bridge, errors, server
from omarchy_hardware.config import Config, MingMqttBroker, MqttSecurity
from omarchy_hardware.errors import ToolError
from omarchy_hardware.serial_session import SerialSession


@pytest.fixture
def port():
    master, slave = pty.openpty()
    session = SerialSession(os.ttyname(slave), 115200)
    yield master, session
    session.close()
    os.close(master)
    os.close(slave)


def _wait(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _bridge(session, *, interval_ms=200, duration_s=30, max_payload=4096, charge=None, sent=None, stopped=None):
    sent = [] if sent is None else sent
    return bridge.Bridge(
        session, "lab/uno", "local", sent.append, charge or (lambda: None),
        duration_s=duration_s, min_interval_ms=interval_ms, max_payload=max_payload,
        on_stop=(stopped.append if stopped is not None else lambda b: None),
    ), sent


def test_only_json_objects_are_forwarded_reserialised(port):
    master, session = port
    running, sent = _bridge(session, interval_ms=200)
    running.start()
    os.write(master, b'boot text\n{ "t" : 21.5 , "h":40 }\n[1,2]\n')
    assert _wait(lambda: len(sent) == 1)
    running.stop()
    running.join()
    assert json.loads(sent[0]) == {"t": 21.5, "h": 40}
    assert sent[0] == b'{"t":21.5,"h":40}'
    assert "boot text" in running.status()["last_lines"]


def test_bursts_are_rate_limited_keeping_the_newest(port):
    master, session = port
    running, sent = _bridge(session, interval_ms=500)
    running.start()
    os.write(master, b"".join(b'{"n":%d}\n' % n for n in range(5)))
    assert _wait(lambda: len(sent) == 2, timeout=3)
    running.stop()
    running.join()
    assert [json.loads(m)["n"] for m in sent] == [0, 4]
    assert running.status()["dropped"]["rate"] == 3


def test_budget_and_size_drops(port):
    master, session = port

    def spent():
        raise ToolError(errors.RATE_LIMITED, "budget")

    running, sent = _bridge(session, charge=spent, max_payload=20)
    running.start()
    os.write(master, b'{"a":1}\n')
    time.sleep(0.3)
    os.write(master, b'{"long":"' + b"x" * 50 + b'"}\n')
    assert _wait(lambda: running.status()["dropped"]["size"] == 1)
    running.stop()
    running.join()
    assert sent == []
    assert running.status()["dropped"]["budget"] == 1


def test_bridge_stops_itself_after_its_duration_and_on_session_close(port):
    _master, session = port
    stopped = []
    running, _ = _bridge(session, duration_s=0, stopped=stopped)
    running.start()
    running.join()
    assert stopped and running.stop_reason == "duration reached" and not running.running

    running, _ = _bridge(session, stopped=stopped)
    running.start()
    session.close()
    running.join()
    assert running.stop_reason == "serial session closed"


def test_registry_limits(port):
    _master, session = port
    registry = bridge.Registry()
    first, _ = _bridge(session)
    registry.add(first)
    second, _ = _bridge(session)
    with pytest.raises(ToolError) as caught:
        registry.add(second)
    assert caught.value.code == errors.PORT_BUSY
    first.stop()
    first.join()
    assert registry.by_port(session.port) is None


BROKER = MingMqttBroker("local", "127.0.0.1", 1883, publish=("lab/uno",), security=MqttSecurity(tls=False))


@pytest.fixture
def ming(monkeypatch, tmp_path):
    monkeypatch.setattr(audit, "STATE_DIR", tmp_path / "state")
    audit._chain.reset()
    monkeypatch.setattr(server, "_ming_budget", None)
    monkeypatch.setattr(server, "bridges", bridge.Registry())
    config = Config(ming_allow=True, ming_mqtt=(BROKER,))
    monkeypatch.setattr(server, "_config", lambda: config)
    published = []
    monkeypatch.setattr(server.ming, "mqtt_publish",
                        lambda target, topic, payload, **kw: published.append((topic, payload)))
    return published


def test_tool_needs_confirm_and_an_allowlisted_topic(ming, port, monkeypatch):
    _master, session = port
    monkeypatch.setattr(server.sessions, "get", lambda session_id: session)
    assert server.serial_bridge_start("s", "lab/uno")["error"]["code"] == errors.UNCONFIRMED
    other = server.serial_bridge_start("s", "lab/other", confirm=True)
    assert other["error"]["code"] == errors.HOST_NOT_ALLOWED
    bad = server.serial_bridge_start("s", "lab/uno", duration_s=99999, confirm=True)
    assert bad["error"]["code"] == errors.INVALID_ARGUMENT


def test_tool_end_to_end(ming, port, monkeypatch):
    master, session = port
    monkeypatch.setattr(server.sessions, "get", lambda session_id: session)
    started = server.serial_bridge_start("s", "lab/uno", min_interval_ms=200, confirm=True)
    assert started["ok"] is True and started["running"] is True

    os.write(master, b'{"t":20}\n')
    assert _wait(lambda: len(ming) == 1)
    assert ming[0] == ("lab/uno", b'{"t":20}')

    blocked = server.serial_read("s")
    assert blocked["error"]["code"] == errors.PORT_BUSY
    assert server.serial_expect("s", "x")["error"]["code"] == errors.PORT_BUSY
    assert server.serial_bridge_status()["bridges"][0]["published"] == 1

    stopped = server.serial_bridge_stop(started["bridge_id"])
    assert stopped["running"] is False and stopped["stop_reason"] == "stopped by request"
    events = [json.loads(line)["event"] for line in audit.log_path().read_text().splitlines()]
    assert events == ["serial_bridge_started", "serial_bridge_stopped"]
    assert server.serial_read("s", max_wait_ms=50)["ok"] is True, "reads work again once the bridge stops"
    assert server.serial_bridge_stop("nope")["error"]["code"] == errors.SESSION_NOT_FOUND
