#!/bin/bash
# One-time setup for the Hardware plugin.
#
# Everything here is deliberately visible and re-runnable. Omarchy's plugin
# system has no install hooks, so this is a script you run yourself rather than
# something that happens behind your back on install.
#
# It does exactly four things, and nothing else:
#   1. adds your user to the serial group (one `sudo usermod`)
#   2. creates a Python virtualenv under ~/.local/share/omarchy-hardware and
#      installs the hash-pinned dependencies from mcp/requirements.lock into it
#   3. checks whether arduino-cli is present at the path the MCP server uses
#   4. writes a default config file (mode 600) if none exists
#
# It never writes udev rules, never installs a systemd unit, never edits
# sudoers, never installs third-party executables, never registers anything
# with Claude Code or any other agent, and never downloads anything into a shell.
#
# --check reports state as JSON without changing anything (via doctor.sh).
# --dry-run prints the commands that would run and exits 0 without mutating.

set -euo pipefail

TRUSTED_PATH="/usr/bin:/bin:/usr/sbin:/sbin"
export PATH="$TRUSTED_PATH"
PYTHON3="/usr/bin/python3"
SUDO="/usr/bin/sudo"
USERMOD="/usr/sbin/usermod"
PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${XDG_DATA_HOME:-$HOME/.local/share}/omarchy-hardware/venv"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy-hardware"
CONFIG="$CONFIG_DIR/config.toml"
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
if [[ ! -x $PYTHON3 ]]; then
  echo "    $PYTHON3 is required but was not found." >&2
  exit 1
fi
echo "    python3: $PYTHON3"
# Same path the MCP server executes (gpio_ssh.py), not a PATH lookup.
SSH_BIN="${OMARCHY_HARDWARE_SSH:-/usr/bin/ssh}"
if [[ $SSH_BIN != /* ]]; then
  echo "    OMARCHY_HARDWARE_SSH must be an absolute path (got: $SSH_BIN)" >&2
  exit 1
elif [[ -x $SSH_BIN ]]; then
  echo "    ssh: $SSH_BIN"
else
  echo "    ssh: missing at $SSH_BIN (GPIO over SSH will not work until openssh is installed)"
fi
if $DRY_RUN; then
  echo "    mode: dry-run (no sudo, pip, or file writes)"
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
  if ! $DRY_RUN && [[ ! -x $SUDO ]]; then
    echo "    sudo is required to add $USER to '$serial_group'." >&2
    exit 1
  fi
  run "$SUDO" "$USERMOD" -aG "$serial_group" "$USER"
  if ! $DRY_RUN; then
    echo "    Done. You must log out and back in for this to take effect."
  fi
fi

# --- 2. python environment ---------------------------------------------------

step "Python environment"
if [[ ! -x $VENV/bin/python ]]; then
  echo "    Creating virtualenv at $VENV"
  run "$PYTHON3" -m venv "$VENV"
else
  skip "virtualenv exists"
fi

if [[ -x $VENV/bin/python ]] && "$VENV/bin/python" -c "import mcp, serial, asyncua" >/dev/null 2>&1; then
  skip "mcp, pyserial and asyncua installed"
elif $DRY_RUN; then
  echo "    dry-run: $VENV/bin/pip install --require-hashes -r $PLUGIN_DIR/mcp/requirements.lock"
  echo "    dry-run: $VENV/bin/pip install --no-deps --no-build-isolation -e $PLUGIN_DIR/mcp"
else
  # Two steps on purpose. Dependencies come from the hash-pinned lock so the
  # install is reproducible and tampering is detected. pip refuses to combine
  # --require-hashes with an editable install ("no single file to hash"), so the
  # plugin itself is installed separately with --no-deps.
  #
  # --no-build-isolation stops pip from downloading an unpinned setuptools into a
  # throwaway build environment. The build backend comes from the lock instead,
  # so the second step needs nothing from the network.
  echo "    Installing pinned dependencies (hash-verified)"
  "$VENV/bin/pip" install --quiet --require-hashes -r "$PLUGIN_DIR/mcp/requirements.lock"
  echo "    Installing the MCP server"
  "$VENV/bin/pip" install --quiet --no-deps --no-build-isolation -e "$PLUGIN_DIR/mcp"
fi

# --- 3. arduino-cli ----------------------------------------------------------

step "arduino-cli"
# Check the exact path the MCP server executes (flash.py), not whatever
# arduino-cli happens to be on PATH, so setup never reports a tool the server
# cannot use.
ARDUINO_BIN="${OMARCHY_HARDWARE_ARDUINO_CLI:-/usr/local/bin/arduino-cli}"
if [[ $ARDUINO_BIN != /* ]]; then
  echo "    OMARCHY_HARDWARE_ARDUINO_CLI must be an absolute path (got: $ARDUINO_BIN)" >&2
  exit 1
elif [[ -x $ARDUINO_BIN ]]; then
  skip "arduino-cli is installed at $ARDUINO_BIN"
else
  echo "    arduino-cli was not found at $ARDUINO_BIN. Install a trusted, pinned release"
  echo "    there, or set OMARCHY_HARDWARE_ARDUINO_CLI to its absolute path, to enable"
  echo "    compiling and flashing; serial and GPIO work without it."
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
# Cap on state-changing GPIO operations per pin per minute. What wears a relay
# is the number of transitions, not the amount of data.
actuation_budget_per_min = 120

[serial]
max_write_bytes = 4096
write_budget_bytes_per_min = 65536
# Writes to unidentified USB-serial adapters (CH340, generic Espressif USB, …).
allow_unknown = false

[flash]
allow = false
# Required when allow = true. Compile and upload may only use these directories.
# sketch_roots = ["~/Arduino"]

[jetson]
hosts = []

# Weintek cMT/MT HMI. Off until you allowlist exact OPC UA nodes and MQTT topics.
# Signing, encryption and TLS are the defaults; reaching an HMI without them
# needs allow_insecure = true on that exact target, so it is a decision someone
# made rather than one nobody noticed.
# [weintek]
# allow = false
# [[weintek.opcua]]
# endpoint = "opc.tcp://192.168.1.50:4840"
# nodes = ["ns=2;s=Temperature"]
# [weintek.opcua.security]
# policy = "Basic256Sha256"
# mode = "SignAndEncrypt"
# certificate = "~/.config/omarchy-hardware/pki/client.der"
# private_key = "~/.config/omarchy-hardware/pki/client.key"
# The HMI's own OPC UA server certificate, exported from the panel. Required for
# Sign/SignAndEncrypt: without it the channel would trust any server at that address.
# trust_list = "~/.config/omarchy-hardware/pki/hmi-server.der"
#
# [[weintek.mqtt]]
# host = "192.168.1.50"
# port = 8883
# topics = ["cMT/machine/temp"]
# [weintek.mqtt.security]
# tls = true
# ca_file = "/etc/ssl/certs/plant-ca.pem"
# A password is named, never stored: password_env points at an environment
# variable the MCP server reads at connect time.
# username = "operator"
# password_env = "OMARCHY_HARDWARE_MQTT_PASSWORD"
#
# Read HMI memory over Modbus TCP, from a project running the MODBUS Server
# driver. Read-only; Modbus has no authentication or encryption, hence the waiver.
# [[weintek.modbus]]
# host = "192.168.1.50"
# allow_insecure = true
# read = ["LW-100:16", "LB-0:32"]

# MING stack: MQTT, InfluxDB, Node-RED, Grafana -- on this machine or another.
# Off until allow = true. Each target has a name; tools take that name, and URLs
# and hostnames are never shown to the model. Cleartext (tls = false, http://) is
# accepted for 127.0.0.1/localhost only; anything remote needs TLS, or an explicit
# waiver with allow_insecure. Tokens and passwords are named here, never stored:
# use *_env for an environment variable or *_file for a mode-600 file.
# examples/ming-stack in the plugin folder runs the whole stack in Docker.
# [ming]
# allow = false
# write_budget_per_min = 60
# [[ming.mqtt]]
# name = "local"
# host = "127.0.0.1"
# subscribe = ["sensors/#"]           # filters; a tool may narrow them, never widen
# publish = ["actuators/fan"]         # exact topics only
# security = { tls = false }
# [[ming.influxdb]]
# name = "local"
# url = "http://127.0.0.1:8086"
# org = "home"
# read_buckets = ["sensors"]
# write_buckets = ["claude"]
# security = { token_file = "~/.config/omarchy-hardware/influxdb-token" }
# [[ming.nodered]]
# name = "local"
# url = "http://127.0.0.1:1880"
# inject_nodes = []                   # ids from the nodered_flows tool
# [[ming.grafana]]
# name = "local"
# url = "http://127.0.0.1:3000"
# annotate = false
# security = { token_file = "~/.config/omarchy-hardware/grafana-token" }
TOML
  chmod 600 "$CONFIG"
  echo "    Wrote $CONFIG (mode 600)"
  echo "    Add your Pi's hostname to [pi] hosts to enable the GPIO tools."
fi

# --- done --------------------------------------------------------------------

step "Result"
if $DRY_RUN; then
  echo "    dry-run complete; no files, groups, or packages were changed."
else
  "$PLUGIN_DIR/bin/doctor.sh"
  echo
  echo "Register the MCP server through your own Claude/Omarchy configuration if needed."
  echo "Hardware changes are logged to ${XDG_STATE_HOME:-$HOME/.local/state}/omarchy-hardware/audit.log."
  echo "Re-run this script any time; every setup step above is skipped when already done."
fi
