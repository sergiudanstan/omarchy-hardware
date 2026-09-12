#!/bin/bash
# Reports what still needs setting up, as one JSON line.
# Shared by the bar panel and by `setup.sh --check` so both agree.

set -uo pipefail

export PATH="/usr/bin:/bin:/usr/sbin:/sbin"
VENV="${XDG_DATA_HOME:-$HOME/.local/share}/omarchy-hardware/venv"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy-hardware/config.toml"

problems=()
pending_relogin=false

add() { problems+=("{\"id\":\"$1\",\"label\":\"$2\"}"); }

# Arch uses uucp for serial device access; dialout only exists on Debian-likes.
serial_group=""
for group in uucp dialout; do
  if getent group "$group" >/dev/null 2>&1; then
    serial_group="$group"
    break
  fi
done

if [[ -n $serial_group ]]; then
  if ! id -nG "$USER" 2>/dev/null | tr ' ' '\n' | grep -qx "$serial_group"; then
    add "group" "Add your user to the '$serial_group' group for serial access"
  elif ! id -nG 2>/dev/null | tr ' ' '\n' | grep -qx "$serial_group"; then
    # The group database has it but this login session predates the change, so
    # the running shell (and the bar) still lack the permission.
    pending_relogin=true
    add "relogin" "Log out and back in to pick up '$serial_group' membership"
  fi
fi

if [[ ! -x $VENV/bin/python ]]; then
  add "venv" "Create the Python environment for the MCP server"
elif ! "$VENV/bin/python" -c "import mcp, serial" >/dev/null 2>&1; then
  add "deps" "Install the MCP server dependencies"
fi

ARDUINO_BIN="${OMARCHY_HARDWARE_ARDUINO_CLI:-/usr/local/bin/arduino-cli}"
if ! command -v arduino-cli >/dev/null 2>&1 && [[ ! -x $ARDUINO_BIN ]]; then
  add "arduino-cli" "Install a trusted pinned arduino-cli release to compile and flash sketches"
fi

[[ -f $CONFIG ]] || add "config" "Write the default config file"

ready=false
((${#problems[@]} == 0)) && ready=true

printf '{"ready":%s,"pending_relogin":%s,"problems":[%s]}\n' \
  "$ready" "$pending_relogin" "$(IFS=,; echo "${problems[*]-}")"
