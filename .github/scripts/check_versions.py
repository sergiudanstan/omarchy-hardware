#!/usr/bin/env python3
"""Fail if the three version strings disagree.

The plugin version is declared in three places that are read by three different
consumers -- the Omarchy shell, pip, and the MCP server's own handshake. A
release where they disagree misreports itself to at least one of them.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

manifest_version = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))["version"]

with (ROOT / "mcp" / "pyproject.toml").open("rb") as handle:
    pyproject_version = tomllib.load(handle)["project"]["version"]

init_text = (ROOT / "mcp" / "omarchy_hardware" / "__init__.py").read_text(encoding="utf-8")
match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', init_text, re.MULTILINE)
if match is None:
    print("could not find __version__ in omarchy_hardware/__init__.py", file=sys.stderr)
    sys.exit(1)
init_version = match.group(1)

versions = {
    "manifest.json": manifest_version,
    "mcp/pyproject.toml": pyproject_version,
    "omarchy_hardware/__init__.py": init_version,
}

if len(set(versions.values())) != 1:
    print("version strings disagree:", file=sys.stderr)
    for source, value in versions.items():
        print(f"  {source}: {value}", file=sys.stderr)
    sys.exit(1)

print(f"version OK -- all three declare {init_version}")
