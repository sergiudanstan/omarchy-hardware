#!/bin/bash
# Emits one JSON line describing connected USB serial boards, for the bar widget.
# Uses the system python and the standard library only, so it keeps working
# before setup.sh has created the virtualenv.

set -uo pipefail

# The bar widget runs this every few seconds inside omarchy-shell, unattended.
# Pin PATH and the interpreter exactly as setup.sh and doctor.sh do, so a pyenv
# shim or ~/.local/bin/python3 earlier in the user's PATH cannot become the
# thing that executes plugin code on a timer.
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
PYTHON3="/usr/bin/python3"

PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! -x $PYTHON3 ]]; then
  printf '{"ok":false,"boards":[],"error":"%s is missing"}\n' "$PYTHON3"
  exit 0
fi

# -B keeps __pycache__ out of the plugin folder: the shell watches this directory
# and treats any new file as a plugin change worth reloading.
if ! output=$(PYTHONPATH="$PLUGIN_DIR/mcp" "$PYTHON3" -B -s -m omarchy_hardware.boards 2>&1); then
  # The widget polls this on a timer; a hard failure must still parse as JSON
  # or the bar would silently freeze on stale data.
  printf '{"ok":false,"boards":[],"error":%s}\n' "$(printf '%s' "$output" | "$PYTHON3" -I -c 'import json,sys; print(json.dumps(sys.stdin.read()[:400]))' 2>/dev/null || echo '"scan failed"')"
  exit 0
fi

printf '%s\n' "$output"
