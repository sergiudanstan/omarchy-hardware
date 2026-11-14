"""Metadata and CLI coverage: no serial or network hardware access."""

import asyncio
import json
from pathlib import Path

import pytest

from omarchy_hardware import board_profiles, boards, cli, displays, reference, server, support
from omarchy_hardware.config import Config, ConfigError

PROFILE = "lilygo_t_qt_pro"


@pytest.fixture
def no_io(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("metadata must not access devices")
    monkeypatch.setattr(server.espchip, "probe", forbidden)
    monkeypatch.setattr(server.sessions, "open", forbidden)
    monkeypatch.setattr(server, "enumerate_boards", forbidden)
    monkeypatch.setattr(boards, "all_boards", forbidden)
    monkeypatch.setattr(reference, "load", Config)
    monkeypatch.setattr(server, "_config", Config)
    monkeypatch.setattr(cli, "load", Config)


def test_device_reference_facts_and_limits(no_io):
    result = reference.device_reference(PROFILE)
    profile = board_profiles.export(PROFILE)
    assert result["format_version"] == 1 and result["format"] == "omarchy-hardware-device-reference"
    assert result["device"]["build"] == profile["build"]
    assert result["device"]["validation"] == profile["validation"]
    assert result["sources"] == profile["sources"] and result["data_status"] == profile["data_status"]
    assert result["limits"]["current_ma"] == profile["current_ma"]
    assert result["limits"]["reserved_pins"] == profile["reserved_pins"]
    assert "GPIO4" in result["measures"][0]["pins"]
    assert {r["kind"] for r in result["measures"]} >= {"adc", "button", "battery"}
    display = next(r for r in result["adjusts"] if r["kind"] == "display")
    assert (display["controller"], display["width"], display["height"]) == ("GC9107", 128, 128)
    assert next(r for r in result["adjusts"] if r["kind"] == "backlight")["active"] == "low"
    assert result["adjusts"][0]["digital_output_count"] > 0
    assert result["operations"] == reference.export_reference(Config())["families"]["microcontroller"]
    assert result["access"]["mcp"]["resource"] == f"hardware://devices/{PROFILE}"
    policy = result["limits"]["enforced_policy"]
    assert not policy["flash"]["allow"] and not policy["flash"]["default_allow"]
    assert {"compile_token", "flash_budget", "identified_board_with_live_chip_probe_for_native_usb"} <= set(
        policy["flash"]["upload_requires"])
    assert policy["serial"]["write_budget_bytes_per_min"] == Config().write_budget_bytes_per_min


def test_legacy_profile_omits_optional_device_keys_and_pi_operations(no_io):
    result = reference.device_reference("raspberry_pi_40pin")
    assert not {"identity", "build", "validation"} & result["device"].keys()
    assert "gpio.read" in {r["id"] for r in result["operations"]}
    assert "flash.upload" not in {r["id"] for r in result["operations"]}


def test_reference_v2_preserves_keys_adds_catalog_and_policy_refresh(no_io):
    result = reference.export_reference(Config(max_uploads_per_hour=3))
    assert result["format_version"] == 2
    assert {"format", "package_version", "mhs", "scope", "families", "policy_snapshot", "limitations"} <= result.keys()
    device = next(d for d in result["devices"] if d["id"] == PROFILE)
    assert device == {"id": PROFILE, "name": "LilyGO T-QT Pro", "chip": "ESP32-S3", "display": "GC9107 128x128"}
    assert result["policy_snapshot"]["flash"]["max_uploads_per_hour"] == 3
    assert not result["mhs"]["compatible"]


def test_markdown_retains_all_leaf_values(no_io):
    result = reference.device_reference(PROFILE)
    rendered = reference.render_markdown(result)
    assert rendered.startswith("# LilyGO T-QT Pro\n")
    assert "## Measures" in rendered and "## Operations" in rendered

    def leaves(value):
        if isinstance(value, dict):
            for item in value.values():
                yield from leaves(item)
        elif isinstance(value, list):
            for item in value:
                yield from leaves(item)
        elif isinstance(value, str):
            yield value

    for value in leaves(result):
        assert value in rendered or json.dumps(value, ensure_ascii=False) in rendered


def test_mcp_reference_json_markdown_resource_and_displays(no_io):
    expected = reference.device_reference(PROFILE)
    assert server.device_reference(PROFILE)["reference"] == expected
    assert server.device_reference(PROFILE, format="markdown")["reference"] == reference.render_markdown(expected)
    assert json.loads(server.device_reference_resource(PROFILE)) == expected
    assert server.display_reference()["displays"] == displays.catalog()
    assert server.display_reference("st7789")["display"] == displays.lookup("ST7789")
    assert not server.display_reference("not-a-controller")["ok"]
    assert not server.device_reference(PROFILE, format="html")["ok"]
    assert not server.device_reference()["ok"]
    assert not server.device_reference(PROFILE, port="/dev/ttyACM0")["ok"]
    assert not server.device_reference("missing")["ok"]


def test_device_tool_port_resolution(no_io, monkeypatch):
    profile = board_profiles.export(PROFILE)
    calls = []

    def resolve(port, fqbn, profile_id):
        calls.append((port, fqbn, profile_id))
        return profile, None

    monkeypatch.setattr(server, "_resolve_profile", resolve)
    assert server.device_reference(port="/dev/ttyACM0")["reference"]["device"]["id"] == PROFILE
    assert calls == [("/dev/ttyACM0", None, None)]


def test_new_tool_annotations():
    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    assert tools["device_reference"].annotations.read_only_hint
    assert tools["display_reference"].annotations.read_only_hint
    assert tools["board_profile_assign"].annotations.read_only_hint is False
    assert tools["board_profile_assign"].annotations.destructive_hint is False


@pytest.mark.parametrize(("args", "key"), [
    (["profiles"], "profiles"), (["profile", PROFILE], "profile"),
    (["reference"], "reference"), (["reference", "--device", PROFILE], "reference"),
    (["displays"], "displays"), (["displays", "ST7789"], "display"),
])
def test_cli_json_commands(no_io, capsys, args, key):
    assert cli.main(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] and key in payload


def test_cli_boards_matches_merged_mcp_view(monkeypatch, capsys):
    fake = [{"port": "/dev/ttyACM0", "label": "bench", "profile_id": PROFILE}]
    monkeypatch.setattr(boards, "all_boards", lambda: fake)
    monkeypatch.setattr(server, "enumerate_boards", lambda: fake)
    assert cli.main(["boards"]) == 0
    assert json.loads(capsys.readouterr().out) == server.list_boards()


def test_cli_markdown_and_errors(no_io, capsys, monkeypatch):
    assert cli.main(["reference", "--device", PROFILE, "--markdown"]) == 0
    assert capsys.readouterr().out.startswith("# LilyGO T-QT Pro")
    assert cli.main(["profile", "missing"]) == 1
    assert not json.loads(capsys.readouterr().out)["ok"]
    assert cli.main(["upload"]) == 2
    capsys.readouterr()

    def invalid():
        raise ConfigError("bad config")

    monkeypatch.setattr(cli, "load", invalid)
    assert cli.main(["reference"]) == 1
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "CONFIG_ERROR"


def test_matrix_bindings_and_docs_stay_in_step():
    doc = (Path(__file__).resolve().parents[2] / "docs/support-matrix.md").read_text()
    for rows in support.MATRIX.values():
        for row in rows:
            if row["availability"] == support.AVAIL_UNSUPPORTED:
                continue
            names = reference.TOOL_BINDINGS[row["id"]]
            matching = [line for line in doc.splitlines() if line.startswith(f'| `{row["id"]}` |')]
            assert matching
            assert all(any(f"`{name}`" in line for line in matching) for name in names)
            assert any(row["safety"] in line and f'| {"yes" if row["requires_confirmation"] else "no"} |' in line
                       for line in matching)
