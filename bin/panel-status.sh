#!/bin/bash
# Emits one JSON line with the configured targets and the audit log's state, for
# the bar panel. Reads config.toml and audit.log only; contacts nothing.

set -uo pipefail

# Same interpreter pinning as scan-boards.sh: the panel runs this unattended.
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
PYTHON3="/usr/bin/python3"

PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! -x $PYTHON3 ]]; then
  printf '{"ok":false,"config_error":"%s is missing","targets":null,"audit":null}\n' "$PYTHON3"
  exit 0
fi

# -B keeps __pycache__ out of the plugin folder, which the shell watches.
if ! output=$(PYTHONPATH="$PLUGIN_DIR/mcp" "$PYTHON3" -B -s -m omarchy_hardware.panel_status 2>&1); then
  printf '{"ok":false,"config_error":%s,"targets":null,"audit":null}\n' "$(printf '%s' "$output" | "$PYTHON3" -I -c 'import json,sys; print(json.dumps(sys.stdin.read()[:400]))' 2>/dev/null || echo '"status failed"')"
  exit 0
fi

printf '%s\n' "$output"
