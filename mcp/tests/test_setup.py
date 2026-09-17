"""setup.sh flags must be safe to run without hardware or sudo."""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SETUP = ROOT / "bin" / "setup.sh"


def _run(*args, env=None):
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(  # noqa: S603
        ["bash", str(SETUP), *args],  # noqa: S607  in-tree setup.sh, not user input
        capture_output=True,
        text=True,
        env=merged,
        check=False,
    )


def test_help_lists_dry_run():
    result = _run("--help")

    assert result.returncode == 0
    assert "--dry-run" in result.stdout
    assert "usermod" in result.stdout


def test_unknown_option_fails():
    result = _run("--explode")

    assert result.returncode != 0
    assert "unknown option" in result.stderr


def test_dry_run_does_not_create_venv_or_config(tmp_path):
    data = tmp_path / "data"
    config = tmp_path / "config"
    result = _run(
        "--dry-run",
        env={
            "HOME": str(tmp_path),
            "XDG_DATA_HOME": str(data),
            "XDG_CONFIG_HOME": str(config),
        },
    )

    assert result.returncode == 0, result.stderr
    assert "dry-run" in result.stdout
    assert "python3:" in result.stdout
    assert not (data / "omarchy-hardware" / "venv").exists()
    assert not (config / "omarchy-hardware" / "config.toml").exists()


DOCTOR = ROOT / "bin" / "doctor.sh"


def _doctor(env):
    import json

    merged = os.environ.copy()
    merged.update(env)
    result = subprocess.run(  # noqa: S603
        ["bash", str(DOCTOR)],  # noqa: S607  in-tree doctor.sh, not user input
        capture_output=True,
        text=True,
        env=merged,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return {problem["id"] for problem in json.loads(result.stdout)["problems"]}


def _isolated_env(tmp_path, **extra):
    return {
        "HOME": str(tmp_path),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        **extra,
    }


def _fake_executable(tmp_path, name):
    path = tmp_path / "bin" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o700)
    return path


def test_doctor_accepts_arduino_cli_at_configured_absolute_path(tmp_path):
    cli = _fake_executable(tmp_path, "arduino-cli")

    problems = _doctor(_isolated_env(tmp_path, OMARCHY_HARDWARE_ARDUINO_CLI=str(cli)))

    assert "arduino-cli" not in problems


def test_doctor_reports_arduino_cli_missing_at_configured_path(tmp_path):
    # Only the path the MCP server executes counts; setup never falls back to PATH.
    missing = tmp_path / "nowhere" / "arduino-cli"

    problems = _doctor(_isolated_env(tmp_path, OMARCHY_HARDWARE_ARDUINO_CLI=str(missing)))

    assert "arduino-cli" in problems


def test_doctor_treats_relative_arduino_cli_override_as_missing(tmp_path):
    _fake_executable(tmp_path, "arduino-cli")
    env = _isolated_env(tmp_path, OMARCHY_HARDWARE_ARDUINO_CLI="bin/arduino-cli")
    merged = os.environ.copy()
    merged.update(env)
    result = subprocess.run(  # noqa: S603
        ["bash", str(DOCTOR)],  # noqa: S607  in-tree doctor.sh, not user input
        capture_output=True,
        text=True,
        env=merged,
        cwd=tmp_path,
        check=False,
    )

    assert '"id":"arduino-cli"' in result.stdout


def test_dry_run_rejects_relative_tool_overrides(tmp_path):
    for variable in ("OMARCHY_HARDWARE_ARDUINO_CLI", "OMARCHY_HARDWARE_SSH"):
        result = _run("--dry-run", env=_isolated_env(tmp_path, **{variable: "relative/tool"}))

        assert result.returncode != 0
        assert f"{variable} must be an absolute path" in result.stderr


def test_dry_run_reports_configured_tool_paths(tmp_path):
    cli = _fake_executable(tmp_path, "arduino-cli")
    ssh = _fake_executable(tmp_path, "ssh")

    result = _run(
        "--dry-run",
        env=_isolated_env(tmp_path, OMARCHY_HARDWARE_ARDUINO_CLI=str(cli), OMARCHY_HARDWARE_SSH=str(ssh)),
    )

    assert result.returncode == 0, result.stderr
    assert f"ssh: {ssh}" in result.stdout
    assert f"arduino-cli is installed at {cli}" in result.stdout
    assert "--no-build-isolation" in result.stdout
    assert "mise" not in result.stdout


def _fake_usb(tmp_path, vid, pid):
    device = tmp_path / "usb" / "1-4"
    device.mkdir(parents=True)
    for attr, value in (("idVendor", vid), ("idProduct", pid), ("busnum", "250"), ("devnum", "250")):
        (device / attr).write_text(value + "\n", encoding="utf-8")
    return tmp_path / "usb"


def test_doctor_ignores_stm32_without_stm32_device(tmp_path):
    usb = _fake_usb(tmp_path, "2341", "0043")

    problems = _doctor(_isolated_env(tmp_path, OMARCHY_HARDWARE_USB_SYSFS=str(usb)))

    assert not {"stm32-core", "stm32-access"} & problems


def test_doctor_reports_stm32_core_and_probe_access(tmp_path):
    usb = _fake_usb(tmp_path, "0483", "3748")

    problems = _doctor(_isolated_env(tmp_path, OMARCHY_HARDWARE_USB_SYSFS=str(usb)))

    assert {"stm32-core", "stm32-access"} <= problems


def test_doctor_accepts_installed_stm32_core(tmp_path):
    usb = _fake_usb(tmp_path, "0483", "374b")
    (tmp_path / ".arduino15" / "packages" / "STMicroelectronics" / "hardware" / "stm32").mkdir(parents=True)

    problems = _doctor(_isolated_env(tmp_path, OMARCHY_HARDWARE_USB_SYSFS=str(usb)))

    assert "stm32-core" not in problems
    assert "stm32-access" not in problems


def _config_template() -> str:
    """The heredoc setup.sh writes as the default config."""
    text = SETUP.read_text(encoding="utf-8")
    body = text.split("<<'TOML'\n", 1)[1]
    return body.split("\nTOML\n", 1)[0]


def test_default_config_template_is_loadable(monkeypatch, tmp_path):
    """The shipped template must parse with the parser that will read it."""
    import tomllib

    from omarchy_hardware import config

    tomllib.loads(_config_template())

    path = tmp_path / "config.toml"
    path.write_text(_config_template(), encoding="utf-8")
    path.chmod(0o600)
    monkeypatch.setattr(config, "CONFIG_PATH", path)

    loaded = config.load()
    assert loaded.actuation_budget_per_min == 120
    assert loaded.weintek_allow is False


def test_commented_weintek_example_still_parses_when_uncommented(monkeypatch, tmp_path):
    """A user who uncomments the example must get a working, secure config.

    The example is the only guidance most people will read, so it has to survive
    the validation it is demonstrating rather than drift away from it.
    """
    import re
    import tomllib

    from omarchy_hardware import config

    # Uncomment only the lines that are TOML: a table header, or an identifier
    # immediately followed by " = ". Prose that happens to contain an equals
    # sign ("needs allow_insecure = true ...") is left behind.
    toml_line = re.compile(r"^(\[.*\]|[A-Za-z_][A-Za-z0-9_]* = .*)$")
    block = _config_template().split("# [weintek]", 1)
    assert len(block) == 2, "the template no longer carries a Weintek example"

    lines = ["[weintek]"]
    for line in block[1].splitlines():
        stripped = line[2:] if line.startswith("# ") else line.removeprefix("#")
        if toml_line.match(stripped.strip()):
            lines.append(stripped.strip())
    raw = tomllib.loads("\n".join(lines))
    assert "weintek" in raw, "the template no longer carries a Weintek example"

    raw["weintek"]["allow"] = True
    allow, opcua, mqtt = config._parse_weintek(raw)

    assert allow is True
    assert opcua[0].security.mode == "SignAndEncrypt"
    assert opcua[0].security.certificate
    assert mqtt[0].security.tls is True
    assert mqtt[0].port == config.DEFAULT_MQTT_TLS_PORT
