#!/bin/bash
# Shows the "board plugged in" notification and runs the button the user picks.
# Usage: board-arrived.sh /dev/ttyACM0
#
# The bar widget runs this unattended, so the interpreter and PATH are pinned as
# in scan-boards.sh. The user's own PATH is handed on through
# OMARCHY_HARDWARE_USER_PATH, because the Claude session opened from here is the
# user's normal interactive session and needs their tools.

set -uo pipefail

export OMARCHY_HARDWARE_USER_PATH="${PATH:-}"
export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
PYTHON3="/usr/bin/python3"

PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ $# -ne 1 || ! $1 =~ ^/dev/tty(ACM|USB)[0-9]{1,3}$ ]]; then
  echo "usage: $0 /dev/ttyACM0" >&2
  exit 2
fi

# -B keeps __pycache__ out of the plugin folder, which the shell watches.
exec env PYTHONPATH="$PLUGIN_DIR/mcp" "$PYTHON3" -B -s -m omarchy_hardware.arrival "$1"
