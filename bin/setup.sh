#!/bin/bash
# One-time setup for the Hardware plugin.
#
# Everything here is deliberately visible and re-runnable. Omarchy's plugin
# system has no install hooks, so this is a script you run yourself rather than
# something that happens behind your back on install.
#
# It does exactly four privileged-adjacent things, and nothing else:
#   1. adds your user to the serial group (one `sudo usermod`)
#   2. creates a Python virtualenv under ~/.local/share/omarchy-hardware
#   3. installs arduino-cli through Omarchy's own mise helper
#   4. registers the MCP server with Claude Code
#
# It never writes udev rules, never installs a systemd unit, never edits
# sudoers, and never downloads anything into a shell.
#
# --check reports state as JSON without changing anything (via doctor.sh).
# --dry-run prints the commands that would run and exits 0 without mutating.

set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${XDG_DATA_HOME:-$HOME/.local/share}/omarchy-hardware/venv"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy-hardware"
CONFIG="$CONFIG_DIR/config.toml"
MCP_NAME="omarchy-hardware"

PAUSE=false
DRY_RUN=false
for arg in "$@"; do
  case "$arg" in
  --check)
    exec "$PLUGIN_DIR/bin/doctor.sh"
    ;;
  --dry-run)
    DRY_RUN=true
    ;;
  --pause)
    PAUSE=true
    ;;
  -h | --help)
    awk 'NR==1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0"
    exit 0
    ;;
  *)
    echo "unknown option: $arg" >&2
    exit 1
    ;;
  esac
done

finish() {
  local status=$?
  if $PAUSE; then
    echo
    read -n1 -r -p "Press any key to close… "
    echo
  fi
  exit $status
}
trap finish EXIT

step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
skip() { printf '    already done: %s\n' "$1"; }
run() {
  if $DRY_RUN; then
    printf '    dry-run: %s\n' "$*"
    return 0
  fi
  "$@"
}

# --- 0. preflight ------------------------------------------------------------

step "Preflight"
if ! command -v python3 >/dev/null 2>&1; then
  echo "    python3 is required but was not found on PATH." >&2
  exit 1
fi
echo "    python3: $(command -v python3)"
if command -v ssh >/dev/null 2>&1; then
  echo "    ssh: present"
else
  echo "    ssh: missing (GPIO over SSH will not work until openssh is installed)"
fi
if $DRY_RUN; then
  echo "    mode: dry-run (no sudo, pip, mise, or file writes)"
fi

# --- 1. serial group ---------------------------------------------------------

serial_group=""
for group in uucp dialout; do
  if getent group "$group" >/dev/null 2>&1; then
    serial_group="$group"
    break
  fi
done

step "Serial device access"
if [[ -z $serial_group ]]; then
  echo "    no uucp or dialout group on this system; skipping"
elif id -nG "$USER" | tr ' ' '\n' | grep -qx "$serial_group"; then
  skip "you are in the '$serial_group' group"
  if ! id -nG | tr ' ' '\n' | grep -qx "$serial_group"; then
    echo "    NOTE: this login session predates the change — log out and back in."
  fi
else
  echo "    Adding $USER to '$serial_group'. This needs sudo:"
  echo "      sudo usermod -aG $serial_group $USER"
  if ! $DRY_RUN && ! command -v sudo >/dev/null 2>&1; then
    echo "    sudo is required to add $USER to '$serial_group'." >&2
    exit 1
  fi
  run sudo usermod -aG "$serial_group" "$USER"
  if ! $DRY_RUN; then
    echo "    Done. You must log out and back in for this to take effect."
  fi
fi

# --- 2. python environment ---------------------------------------------------

step "Python environment"
if [[ ! -x $VENV/bin/python ]]; then
  echo "    Creating virtualenv at $VENV"
  run python3 -m venv "$VENV"
else
  skip "virtualenv exists"
fi

if [[ -x $VENV/bin/python ]] && "$VENV/bin/python" -c "import mcp, serial" >/dev/null 2>&1; then
  skip "mcp and pyserial installed"
elif $DRY_RUN; then
  echo "    dry-run: $VENV/bin/pip install --require-hashes -r $PLUGIN_DIR/mcp/requirements.lock"
  echo "    dry-run: $VENV/bin/pip install --no-deps -e $PLUGIN_DIR/mcp"
else
  # Two steps on purpose. Dependencies come from the hash-pinned lock so the
  # install is reproducible and tampering is detected. pip refuses to combine
  # --require-hashes with an editable install ("no single file to hash"), so the
  # plugin itself is installed separately with --no-deps.
  echo "    Installing pinned dependencies (hash-verified)"
  "$VENV/bin/pip" install --quiet --require-hashes -r "$PLUGIN_DIR/mcp/requirements.lock"
  echo "    Installing the MCP server"
  "$VENV/bin/pip" install --quiet --no-deps -e "$PLUGIN_DIR/mcp"
fi

# --- 3. arduino-cli ----------------------------------------------------------

step "arduino-cli"
if command -v arduino-cli >/dev/null 2>&1; then
  skip "arduino-cli is installed"
elif command -v omarchy-mise-install >/dev/null 2>&1; then
  echo "    Installing via Omarchy's mise helper"
  run omarchy-mise-install arduino-cli
else
  echo "    omarchy-mise-install not found. Install arduino-cli yourself to enable"
  echo "    compiling and flashing; serial and GPIO work without it."
fi

if ! $DRY_RUN && command -v arduino-cli >/dev/null 2>&1; then
  arduino-cli core update-index >/dev/null 2>&1 || true
fi

# --- 4. config ---------------------------------------------------------------

step "Configuration"
if [[ -f $CONFIG ]]; then
  skip "$CONFIG exists"
elif $DRY_RUN; then
  echo "    dry-run: write $CONFIG mode 600"
else
  mkdir -p "$CONFIG_DIR"
  chmod 700 "$CONFIG_DIR"
  umask 077
  cat >"$CONFIG" <<'TOML'
# Which Raspberry Pi hosts this plugin may reach over SSH. Nothing outside this
# list can be contacted, and there is no tool that runs arbitrary commands on
# them -- only fixed pinctrl/raspi-gpio verbs. The host key must already exist
# in ~/.ssh/known_hosts; new keys are not accepted automatically.
[pi]
hosts = []
# BCM pins the GPIO tools may touch. 0 and 1 are the HAT ID EEPROM pins and are
# left out on purpose.
allowed_pins = [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27]
ssh_timeout = 10

[serial]
max_write_bytes = 4096
write_budget_bytes_per_min = 65536
# Writes to unidentified USB-serial adapters (CH340, generic Espressif USB, …).
allow_unknown = false

[flash]
allow = false
# Required when allow = true. Compile and upload may only use these directories.
# sketch_roots = ["~/Arduino"]

# Weintek cMT/MT HMI. Off until you allowlist exact OPC UA nodes and MQTT topics.
# [weintek]
# allow = false
# [[weintek.opcua]]
# endpoint = "opc.tcp://192.168.1.50:4840"
# nodes = ["ns=2;s=Temperature"]
# [[weintek.mqtt]]
# host = "192.168.1.50"
# port = 1883
# topics = ["cMT/machine/temp"]
TOML
  chmod 600 "$CONFIG"
  echo "    Wrote $CONFIG (mode 600)"
  echo "    Add your Pi's hostname to [pi] hosts to enable the GPIO tools."
fi

# --- 5. claude registration --------------------------------------------------

step "Claude Code MCP registration"
if ! command -v claude >/dev/null 2>&1; then
  echo "    Claude Code is not installed; skipping registration."
elif claude mcp get "$MCP_NAME" >/dev/null 2>&1; then
  skip "'$MCP_NAME' is registered"
else
  echo "    Registering '$MCP_NAME'"
  run claude mcp add --scope user "$MCP_NAME" -- "$PLUGIN_DIR/bin/hardware-mcp"
fi

# --- done --------------------------------------------------------------------

step "Result"
if $DRY_RUN; then
  echo "    dry-run complete; no files, groups, or packages were changed."
else
  "$PLUGIN_DIR/bin/doctor.sh"
  echo
  echo "Re-run this script any time; every step above is skipped when already done."
fi
