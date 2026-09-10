#!/bin/bash
# Emits one JSON line describing connected USB serial boards, for the bar widget.
# Uses the system python and the standard library only, so it keeps working
# before setup.sh has created the virtualenv.

set -uo pipefail

PLUGIN_DIR="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"

# -B keeps __pycache__ out of the plugin folder: the shell watches this directory
# and treats any new file as a plugin change worth reloading.
if ! output=$(PYTHONPATH="$PLUGIN_DIR/mcp" python3 -B -m omarchy_hardware.boards 2>&1); then
  # The widget polls this on a timer; a hard failure must still parse as JSON
  # or the bar would silently freeze on stale data.
  printf '{"ok":false,"boards":[],"error":%s}\n' "$(printf '%s' "$output" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()[:400]))' 2>/dev/null || echo '"scan failed"')"
  exit 0
fi

printf '%s\n' "$output"
