#!/bin/bash
# Emits one JSON line with the configured targets and the audit log's state, for
# the bar panel. Reads config.toml and audit.log only; contacts nothing.

set -uo pipefail

# Same interpreter pinning as scan-boards.sh: the panel runs this unattended.
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
PYTHON3="/usr/bin/python3"

PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# A failure of this script is reported as "error", not "config_error": the
# panel labels config_error as a config.toml problem.
if [[ ! -x $PYTHON3 ]]; then
  printf '{"ok":false,"config_error":"","error":"%s is missing","targets":null,"audit":null}\n' "$PYTHON3"
  exit 0
fi

# stderr goes to its own file: a warning printed on a successful run would
# otherwise end up inside the JSON on stdout.
errors=$(mktemp) || exit 0
trap 'rm -f "$errors"' EXIT

# -B keeps __pycache__ out of the plugin folder, which the shell watches.
if ! output=$(PYTHONPATH="$PLUGIN_DIR/mcp" "$PYTHON3" -B -s -m omarchy_hardware.panel_status 2>"$errors"); then
  printf '{"ok":false,"config_error":"","error":%s,"targets":null,"audit":null}\n' "$("$PYTHON3" -I -c 'import json,sys; print(json.dumps(sys.stdin.read()[-400:]))' <"$errors" 2>/dev/null || echo '"status failed"')"
  exit 0
fi

printf '%s\n' "$output"
