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
