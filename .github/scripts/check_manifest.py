#!/usr/bin/env python3
"""Validate manifest.json the way Omarchy's own plugin validator does.

Mirrors the checks in `omarchy-plugin-validate` so CI catches a broken manifest
before a user's shell silently refuses to load the plugin.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
KIND_ENTRY_POINTS = {
    "bar": "bar",
    "bar-widget": "barWidget",
    "menu": "menu",
    "overlay": "overlay",
    "panel": "panel",
    "service": "service",
}

errors: list[str] = []


def fail(message: str) -> None:
    errors.append(message)


manifest_path = ROOT / "manifest.json"
if not manifest_path.is_file():
    print("manifest.json is missing", file=sys.stderr)
    sys.exit(1)

try:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
except json.JSONDecodeError as exc:
    print(f"manifest.json is not valid JSON: {exc}", file=sys.stderr)
    sys.exit(1)

# schemaVersion must be the JSON number 1 -- the string "1" is rejected by the
# shell's registry, and bool is a subclass of int so exclude it explicitly.
schema_version = manifest.get("schemaVersion")
if schema_version != 1 or isinstance(schema_version, bool):
    fail(f"schemaVersion must be the integer 1, got {schema_version!r}")

for field in ("id", "name", "version", "kinds", "entryPoints"):
    if field not in manifest:
        fail(f"missing required field {field!r}")

plugin_id = manifest.get("id", "")
if not plugin_id:
    fail("id is empty")
elif not ID_PATTERN.match(plugin_id):
    fail(f"invalid plugin id {plugin_id!r}")
elif plugin_id.startswith("omarchy."):
    fail(f"plugin id {plugin_id!r} uses the reserved omarchy.* namespace")

kinds = manifest.get("kinds")
if not isinstance(kinds, list) or not kinds:
    fail("kinds must be a non-empty array")
    kinds = []

entry_points = manifest.get("entryPoints")
if not isinstance(entry_points, dict):
    fail("entryPoints must be an object")
    entry_points = {}

for kind in kinds:
    key = KIND_ENTRY_POINTS.get(kind)
    if key is None:
        fail(f"unknown kind {kind!r}")
    elif key not in entry_points:
        fail(f"kind {kind!r} requires entryPoints.{key}")

for key, value in entry_points.items():
    if not isinstance(value, str) or not value:
        fail(f"entryPoints.{key} must be a non-empty string")
        continue
    if value.startswith("/") or ".." in value:
        fail(f"entryPoints.{key} must be a safe relative path, got {value!r}")
        continue
    if not (ROOT / value).is_file():
        fail(f"entryPoints.{key} points at {value!r}, which does not exist")

if errors:
    for error in errors:
        print(f"manifest: {error}", file=sys.stderr)
    sys.exit(1)

print(f"manifest.json OK -- {plugin_id} {manifest.get('version')}")
