import json
import os
import stat

import pytest

from omarchy_hardware import audit, http_lite, ming, ws_lite
from omarchy_hardware import slack_bridge as sb
from omarchy_hardware.config import ConfigError

CHANNEL, READER, OBSERVER, STRANGER = "C0123ABCD", "U0123ABCD", "U0456EFGH", "U0999ZZZZ"


def _secret(tmp_path, name, value):
    path = tmp_path / name
    path.write_text(value + "\n")
    path.chmod(0o600)
    return str(path)


@pytest.fixture
def raw(tmp_path):
    return {"slack": {
        "enabled": True,
        "app_token_file": _secret(tmp_path, "app", "xapp-1-A-secret"),
        "bot_token_file": _secret(tmp_path, "bot", "xoxb-1-secret"),
        "channels": [CHANNEL], "readers": [READER], "observers": [OBSERVER],
    }}


def test_settings_load_and_default_limits(raw):
    settings = sb.load_settings(raw)
    assert settings.users == {READER: "read", OBSERVER: "observe"}
    assert settings.channels == {CHANNEL}
    assert (settings.max_requests_per_hour, settings.max_runtime_s, settings.max_budget_usd) == (20, 180, 1.0)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"enabled": False}, "off"),
        ({"channels": []}, "at least one channel"),
        ({"readers": [], "observers": []}, "nobody is allowed"),
        ({"channels": ["#general"]}, "Slack IDs"),
        ({"readers": ["U0123ABCD"], "observers": ["U0123ABCD"]}, "both reader and observer"),
        ({"max_runtime_s": 9999}, "max_runtime_s"),
        ({"max_budget_usd": 0}, "max_budget_usd"),
        ({"model": "x; rm -rf"}, "model"),
        ({"surprise": 1}, "unknown keys"),
    ],
)
def test_settings_are_validated(raw, change, message):
    raw["slack"].update(change)
    with pytest.raises(ConfigError, match=message):
        sb.load_settings(raw)


def test_tokens_must_be_the_right_kind_and_private(raw, tmp_path, monkeypatch):
    raw["slack"]["app_token_file"] = _secret(tmp_path, "wrong", "xoxb-not-an-app-token")
    with pytest.raises(ConfigError, match="app-level token"):
        sb.load_settings(raw)
    raw["slack"]["app_token_file"] = _secret(tmp_path, "open", "xapp-1-A")
    # Report the file as group-readable rather than making a world-readable one.
    real_fstat = os.fstat

    def group_readable(fd):
        st = real_fstat(fd)
        return os.stat_result((st.st_mode | 0o040, *tuple(st)[1:]))

    monkeypatch.setattr(ming.os, "fstat", group_readable)
    with pytest.raises(ConfigError, match="readable by other users"):
        sb.load_settings(raw)
    with pytest.raises(ConfigError, match="off"):
        sb.load_settings(None)


def test_tiers_never_include_anything_that_changes_hardware():
    annotations = sb.tool_annotations()
    tiers = sb.tier_tools(annotations)
    for tier, names in tiers.items():
        for name in names:
            assert not annotations[name]["destructive"], (tier, name)
            assert annotations[name]["read_only"] or name in sb.OBSERVE_EXTRA, (tier, name)
    never = {"upload_sketch", "serial_write", "serial_query", "gpio_write_pin", "gpio_set_mode", "mqtt_publish",
             "influx_write", "firmware_restore", "mpy_exec", "mpy_put", "serial_bridge_start", "board_label",
             "weintek_opcua_write", "nodered_inject", "grafana_annotate"}
    assert never <= set(annotations), "the list above names real tools"
    assert not never & set(tiers["observe"])
    assert set(tiers["read"]) < set(tiers["observe"])
    assert "serial_open" not in tiers["read"] and "serial_open" in tiers["observe"]
    assert {"list_boards", "board_profile", "wiring_check"} <= set(tiers["read"])


def _settings(**extra):
    return sb.Settings(app_token="xapp-1", bot_token="xoxb-1", channels=frozenset({CHANNEL}),
                       users={READER: "read", OBSERVER: "observe"}, **extra)


def test_claude_argv_is_fenced_and_keeps_the_request_off_the_command_line(tmp_path):
    tools = {"read": ["list_boards"], "observe": ["list_boards", "serial_open"]}
    argv = sb.claude_argv("/usr/bin/claude", "read", tools, ["list_boards", "serial_open", "upload_sketch"],
                          tmp_path / "mcp.json", _settings(model="claude-sonnet-5"))
    joined = " ".join(argv)
    for flag in ("--restricted", "--strict-mcp-config", "--no-session-persistence"):
        assert flag in argv
    assert argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert argv[argv.index("--permission-prompts") + 1] == "none"
    allowed = argv[argv.index("--allowedTools") + 1:argv.index("--disallowedTools")]
    assert allowed == ["mcp__omarchy-hardware__list_boards"]
    denied = argv[argv.index("--disallowedTools") + 1:argv.index("--model")]
    assert set(denied) == {"mcp__omarchy-hardware__serial_open", "mcp__omarchy-hardware__upload_sketch"}
    assert "slack_request" not in joined.split("--append-system-prompt")[0]


class FakeApi:
    def __init__(self):
        self.posts, self.reactions = [], []

    def post(self, channel, thread_ts, text):
        self.posts.append((channel, thread_ts, text))

    def react(self, channel, ts, name):
        self.reactions.append((channel, ts, name))

    def connections_open(self):
        return "wss://example.invalid/link"


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    monkeypatch.setattr(sb, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(audit, "STATE_DIR", tmp_path / "state")
    audit._chain.reset()
    calls = []

    def runner(argv, prompt, timeout, cwd):
        calls.append((argv, prompt, timeout, cwd))
        return sb.Outcome(True, "Two boards: <!channel> Uno & ESP32 <@U1>", 4.2, 0.031)

    made = sb.Bridge(_settings(max_requests_per_hour=2), FakeApi(),
                     {"read": ["list_boards"], "observe": ["list_boards", "serial_open"]},
                     ["list_boards", "serial_open", "upload_sketch"], "/usr/bin/claude", runner=runner,
                     log=lambda line: None)
    made.calls = calls
    return made


def _envelope(user=READER, channel=CHANNEL, text="<@UBOT123> what is plugged in?", event_id="Ev1", ts="1.1"):
    return {"envelope_id": f"env-{event_id}", "type": "events_api",
            "payload": {"event_id": event_id,
                        "event": {"type": "app_mention", "user": user, "channel": channel, "text": text, "ts": ts}}}


def _audit_events():
    return [json.loads(line) for line in audit.log_path().read_text().splitlines()]


def test_a_mention_is_acked_queued_and_answered(bridge):
    acks = []
    bridge.handle_envelope(_envelope(), acks.append)
    assert acks == ['{"envelope_id": "env-Ev1"}']
    assert bridge.api.reactions == [(CHANNEL, "1.1", "eyes")]
    request = bridge.jobs.get_nowait()
    assert request.text == "what is plugged in?" and request.tier == "read" and request.thread_ts == "1.1"

    bridge.work_once(request)
    argv, prompt, timeout, cwd = bridge.calls[0]
    assert "<slack_request>\nwhat is plugged in?\n</slack_request>" in prompt
    assert argv[argv.index("--allowedTools") + 1:argv.index("--disallowedTools")] == [
        "mcp__omarchy-hardware__list_boards"]
    channel, thread, text = bridge.api.posts[-1]
    assert (channel, thread) == (CHANNEL, "1.1")
    assert "&lt;!channel&gt;" in text and "<!channel>" not in text and "&lt;@U1&gt;" in text and "&amp;" in text
    assert text.endswith("_read access · 4 s · $0.03_")
    event = _audit_events()[-1]
    assert event["event"] == "slack_request" and event["outcome"] == "answered" and event["tier"] == "read"
    assert "what is plugged in" not in json.dumps(event), "only a digest of the request is logged"


def test_observers_get_the_wider_tier(bridge):
    bridge.handle_envelope(_envelope(user=OBSERVER), lambda _: None)
    request = bridge.jobs.get_nowait()
    bridge.work_once(request)
    argv = bridge.calls[0][0]
    assert "mcp__omarchy-hardware__serial_open" in argv[argv.index("--allowedTools"):argv.index("--disallowedTools")]


def test_retries_are_deduplicated(bridge):
    bridge.handle_envelope(_envelope(), lambda _: None)
    bridge.handle_envelope(_envelope(), lambda _: None)
    assert bridge.jobs.qsize() == 1


def test_strangers_are_refused_once_an_hour_and_logged(bridge):
    bridge.handle_envelope(_envelope(user=STRANGER, event_id="a"), lambda _: None)
    bridge.handle_envelope(_envelope(user=STRANGER, event_id="b"), lambda _: None)
    assert bridge.jobs.empty()
    assert [p[2] for p in bridge.api.posts] == ["Sorry, you're not on this workstation's Slack access list."]
    assert [e["outcome"] for e in _audit_events()] == ["refused_user", "refused_user"]


def test_unlisted_channels_are_ignored_silently(bridge):
    bridge.handle_envelope(_envelope(channel="C9999ZZZZ"), lambda _: None)
    assert bridge.jobs.empty() and bridge.api.posts == []


def test_rate_limit_empty_text_and_full_queue(bridge, monkeypatch):
    bridge.handle_envelope(_envelope(text="<@UBOT123>", event_id="e0"), lambda _: None)
    assert "Ask me about the boards" in bridge.api.posts[-1][2]
    bridge.handle_envelope(_envelope(event_id="e1"), lambda _: None)
    bridge.handle_envelope(_envelope(event_id="e2"), lambda _: None)
    bridge.handle_envelope(_envelope(event_id="e3"), lambda _: None)
    assert "request limit" in bridge.api.posts[-1][2]
    assert bridge.jobs.qsize() == 2

    bridge.budget = sb.HourlyBudget(100)
    for n in range(sb.QUEUE_LIMIT):
        bridge.handle_envelope(_envelope(event_id=f"f{n}"), lambda _: None)
    assert "busy" in bridge.api.posts[-1][2]
    spent = sum(n for _, n in bridge.budget._events.get(READER, []))
    bridge.handle_envelope(_envelope(event_id="busy-again"), lambda _: None)
    assert "busy" in bridge.api.posts[-1][2]
    assert sum(n for _, n in bridge.budget._events.get(READER, [])) == spent, \
        "a full queue must not spend the hourly request cap"


def test_non_mentions_and_other_envelopes_are_only_acked(bridge):
    acks = []
    bridge.handle_envelope({"envelope_id": "x", "type": "slash_commands", "payload": {}}, acks.append)
    bridge.handle_envelope({"type": "hello"}, acks.append)
    message = _envelope()
    message["payload"]["event"]["type"] = "message"
    bridge.handle_envelope(message, acks.append)
    assert len(acks) == 2 and bridge.jobs.empty()


def test_long_replies_are_truncated(bridge):
    bridge.runner = lambda *a: sb.Outcome(True, "y" * 10_000, 1.0, None)
    bridge.work_once(sb.Request(READER, CHANNEL, "1.1", "1.1", "q", "read"))
    text = bridge.api.posts[-1][2]
    assert len(text) < sb.MAX_REPLY_CHARS + 100 and "…" in text and "$" not in text


FAKE_CLAUDE = """#!/bin/sh
case "$FAKE_MODE" in
  ok) cat > /dev/null; echo '{"type":"result","result":"All good","is_error":false,"total_cost_usd":0.02}';;
  error) cat > /dev/null; echo '{"type":"result","result":"Budget exceeded","is_error":true}'; exit 1;;
  garbage) cat > /dev/null; echo 'not json';;
  slow) sleep 5;;
esac
"""


@pytest.mark.parametrize(
    ("mode", "ok", "text"),
    [("ok", True, "All good"), ("error", False, "Budget exceeded"), ("garbage", False, "readable answer"),
     ("slow", False, "longer than 1 s")],
)
def test_run_claude_outcomes(tmp_path, monkeypatch, mode, ok, text):
    binary = tmp_path / "claude"
    binary.write_text(FAKE_CLAUDE)
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("FAKE_MODE", mode)
    outcome = sb.run_claude([str(binary), "-p"], "question", 1, tmp_path)
    assert outcome.ok is ok and text in outcome.text


def test_slack_api_calls_and_errors():
    calls = []

    def request(method, url, *, headers, body, timeout):
        calls.append((url, headers["Authorization"], json.loads(body)))
        if url.endswith("chat.postMessage"):
            return http_lite.Response(200, b'{"ok": false, "error": "channel_not_found"}')
        if url.endswith("apps.connections.open"):
            return http_lite.Response(200, b'{"ok": true, "url": "wss://wss.slack.test/link?ticket=1"}')
        return http_lite.Response(200, b'{"ok": true}')

    api = sb.SlackApi(_settings(), request=request)
    assert api.connections_open() == "wss://wss.slack.test/link?ticket=1"
    assert calls[0][1] == "Bearer xapp-1", "Socket Mode uses the app-level token"
    with pytest.raises(http_lite.HttpError, match="channel_not_found"):
        api.post(CHANNEL, "1.1", "hi")
    assert calls[1][1] == "Bearer xoxb-1" and calls[1][2]["thread_ts"] == "1.1"
    api.react(CHANNEL, "1.1", "eyes")


def test_serve_reconnects_when_slack_asks(bridge, monkeypatch):
    messages = [json.dumps(_envelope(event_id="s1")), None, json.dumps({"type": "disconnect", "reason": "refresh"})]
    connects = []
    sent = []

    class FakeClient:
        def __init__(self, url):
            connects.append(url)
            if len(connects) > 1:
                raise SystemExit  # Stop the test after the reconnect attempt.

        def recv_text(self, timeout):
            return messages.pop(0)

        def send_text(self, text):
            sent.append(text)

        def close(self):
            sent.append("closed")

    monkeypatch.setattr(bridge, "worker", lambda: None)
    with pytest.raises(SystemExit):
        bridge.serve(connect=FakeClient)
    assert len(connects) == 2 and sent == ['{"envelope_id": "env-s1"}', "closed"]
    assert bridge.jobs.qsize() == 1


def test_serve_backs_off_when_connecting_fails(bridge, monkeypatch):
    sleeps = []

    def fail(url):
        raise ws_lite.WsError("nope")

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 4:
            raise SystemExit

    monkeypatch.setattr(bridge, "worker", lambda: None)
    monkeypatch.setattr(sb.time, "sleep", sleep)
    with pytest.raises(SystemExit):
        bridge.serve(connect=fail)
    assert sleeps == [1.0, 2.0, 4.0, 8.0]


def test_workspace_and_mcp_config(bridge):
    assert stat.S_IMODE(bridge.workdir.stat().st_mode) == 0o700
    config = json.loads(bridge.mcp_config.read_text())
    assert config["mcpServers"]["omarchy-hardware"]["command"].endswith("bin/hardware-mcp")


def test_main_reports_config_errors(monkeypatch, capsys):
    monkeypatch.setattr(sb, "read_raw", lambda: {})
    assert sb.main(["--check"]) == 1
    assert "off" in capsys.readouterr().err


def test_a_slack_error_while_replying_does_not_end_the_bridge(bridge, monkeypatch):
    def refuse(channel, thread_ts, text):
        raise http_lite.HttpError("not_in_channel", 200)

    monkeypatch.setattr(bridge.api, "post", refuse)
    acks = []
    bridge.handle_envelope(_envelope(user=STRANGER, event_id="x1"), acks.append)
    bridge.handle_envelope(_envelope(text="<@UBOT123>", event_id="x2"), acks.append)
    assert len(acks) == 2


def test_serve_survives_an_event_that_raises(bridge, monkeypatch):
    messages = [json.dumps(_envelope(event_id="boom")), json.dumps([1, 2]),
                json.dumps(_envelope(event_id="fine")), json.dumps({"type": "disconnect"})]
    connects = []

    class FakeClient:
        def __init__(self, url):
            connects.append(url)
            if len(connects) > 1:
                raise SystemExit

        def recv_text(self, timeout):
            return messages.pop(0)

        def send_text(self, text):
            pass

        def close(self):
            pass

    seen = []

    def exploding(envelope, ack):
        seen.append(envelope["payload"]["event_id"])
        if envelope["payload"]["event_id"] == "boom":
            raise AttributeError("unexpected payload shape")

    monkeypatch.setattr(bridge, "worker", lambda: None)
    monkeypatch.setattr(bridge, "handle_envelope", exploding)
    with pytest.raises(SystemExit):
        bridge.serve(connect=FakeClient)
    assert seen == ["boom", "fine"]


def test_non_dict_payloads_are_ignored(bridge):
    acks = []
    bridge.handle_envelope({"envelope_id": "e", "type": "events_api", "payload": ["not", "a", "dict"]}, acks.append)
    bridge.handle_envelope({"envelope_id": "f", "type": "events_api", "payload": {"event": "text"}}, acks.append)
    assert len(acks) == 2 and bridge.jobs.qsize() == 0


def test_token_settings_must_be_strings(raw):
    raw["slack"]["app_token_env"] = 7
    with pytest.raises(ConfigError, match="app_token_env must be a string"):
        sb.load_settings(raw)
