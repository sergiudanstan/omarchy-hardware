#!/bin/bash
# Runs the Slack bridge in the foreground: @mentions of the Slack app in listed
# channels are answered by Claude with this workstation's read-only hardware tools.
# Usage: slack-bridge.sh [--check]
#
# Uses the plugin's virtualenv, like hardware-mcp. Claude Code is found at
# OMARCHY_HARDWARE_CLAUDE or ~/.local/bin/claude. See docs/slack.md.

set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${XDG_DATA_HOME:-$HOME/.local/share}/omarchy-hardware/venv/bin/python"

if [[ ! -x $PYTHON ]]; then
  echo "slack-bridge: virtualenv missing. Run $PLUGIN_DIR/bin/setup.sh" >&2
  exit 1
fi

exec env PYTHONPATH="$PLUGIN_DIR/mcp" "$PYTHON" -B -m omarchy_hardware.slack_bridge "$@"
