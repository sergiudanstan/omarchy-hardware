"""Nonregular private files must fail without blocking on a FIFO writer."""
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("reader", ["secret", "config", "parts"])
def test_fifo_rejected_before_reading(tmp_path, reader):
    fifo = tmp_path / "named-pipe"
    os.mkfifo(fifo, 0o600)
    program = """
import sys
from pathlib import Path
from omarchy_hardware import config, ming, parts
from omarchy_hardware.errors import ToolError
path = Path(sys.argv[1])
config.CONFIG_PATH = path
parts.parts_path = lambda: path
readers = {"secret": lambda: ming.read_secret(None, str(path), "fixture"),
           "config": config.read_raw, "parts": parts._read_raw}
try:
    readers[sys.argv[2]]()
except (ToolError, config.ConfigError) as exc:
    assert "regular file" in str(exc)
else:
    raise AssertionError("FIFO was accepted")
"""
    result = subprocess.run([sys.executable, "-c", program, str(fifo), reader],  # noqa: S603 - fixed child program
                            capture_output=True, text=True, timeout=3)
    assert result.returncode == 0, result.stderr
