"""Reference export and MCP interoperability without physical hardware."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from omarchy_hardware import reference, server, support
from omarchy_hardware.config import Config, ConfigError, WeintekMqttTarget, WeintekOpcUaTarget

MCP_ROOT = Path(__file__).resolve().parents[1]


def test_reference_preserves_support_status_and_resolves_bindings():
    exported = reference.export_reference(Config())
    assert exported["mhs"]["compatible"] is False
    assert exported["mhs"]["specification_version"] is None
    for family, rows in exported["families"].items():
        for row, original in zip(rows, support.MATRIX[family], strict=True):
            assert {key: row[key] for key in original} == original
            if row["availability"] != support.AVAIL_UNSUPPORTED:
                assert row["mcp_tools"], row["id"]
            for name in row["mcp_tools"]:
                assert callable(getattr(server, name)), name
    weintek = {row["id"]: row["availability"] for row in exported["families"]["weintek_hmi"]}
    assert weintek.pop("hmi.identify") == "unsupported"
    assert set(weintek.values()) == {"experimental"}


def test_reference_excludes_private_targets_and_paths():
    config = Config(
        pi_hosts=("private-pi.invalid",),
        jetson_hosts=("private-jetson.invalid",),
        sketch_roots=("/private-sketches",),
        weintek_opcua=(WeintekOpcUaTarget("opc.tcp://private-hmi.invalid:4840", ("secret-node",)),),
        weintek_mqtt=(WeintekMqttTarget("private-broker.invalid", 1883, ("secret-topic",)),),
    )
    result = reference.export_reference(config)
    serialized = json.dumps(result)
    for secret in (
        "private-pi",
        "private-jetson",
        "private-sketches",
        "private-hmi",
        "secret-node",
        "private-broker",
        "secret-topic",
    ):
        assert secret not in serialized
    assert result["policy_snapshot"]["pi"]["hosts_configured"] is True


def test_reference_tool_refreshes_policy_without_hardware_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Reference export must not contact hardware")

    monkeypatch.setattr(server, "enumerate_boards", forbidden)
    monkeypatch.setattr(server.sessions, "all", forbidden)
    monkeypatch.setattr(server.gpio_ssh, "inventory", forbidden)
    monkeypatch.setattr(server.jetson_ssh, "inventory", forbidden)
    monkeypatch.setattr(server, "_config", lambda: Config(max_write_bytes=100))
    first = server.get_hardware_reference("microcontroller")
    monkeypatch.setattr(server, "_config", lambda: Config(max_write_bytes=50))
    second = server.get_hardware_reference("microcontroller")
    assert first["reference"]["policy_snapshot"]["serial"]["max_write_bytes"] == 100
    assert second["reference"]["policy_snapshot"]["serial"]["max_write_bytes"] == 50
    assert list(second["reference"]["families"]) == ["microcontroller"]


def test_reference_tool_rejects_unknown_family(monkeypatch):
    monkeypatch.setattr(server, "_config", Config)
    assert server.get_hardware_reference("toaster")["error"]["code"] == "UNSUPPORTED_OPERATION"


def test_invalid_config_does_not_return_default_policy(monkeypatch):
    def invalid():
        raise ConfigError("invalid policy")

    monkeypatch.setattr(server, "load_config", invalid)
    result = server.get_hardware_reference()
    assert result["error"]["code"] == "CONFIG_ERROR"
    assert "reference" not in result


def test_reference_cli_outputs_json(tmp_path):
    result = subprocess.run(
        [sys.executable, "-B", "-m", "omarchy_hardware.reference", "--family", "jetson"],
        env={**os.environ, "PYTHONPATH": str(MCP_ROOT), "XDG_CONFIG_HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert list(payload["reference"]["families"]) == ["jetson"]


def test_reference_over_stdio_mcp(tmp_path):
    async def exchange():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-B", "-m", "omarchy_hardware.server"],
            env={"PYTHONPATH": str(MCP_ROOT), "XDG_CONFIG_HOME": str(tmp_path)},
        )
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as client:
            await client.initialize()
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert tools["get_hardware_reference"].annotations.read_only_hint is True
            result = await client.call_tool("get_hardware_reference", {"family": "microcontroller"})
            assert not result.is_error
            payload = json.loads(next(item.text for item in result.content if item.type == "text"))
            assert payload["ok"] is True
            assert payload["reference"]["mhs"]["compatible"] is False
            for rows in payload["reference"]["families"].values():
                for row in rows:
                    assert set(row["mcp_tools"]) <= tools.keys()

    asyncio.run(asyncio.wait_for(exchange(), timeout=20))
