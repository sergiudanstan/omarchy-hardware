# Hardware — Arduino & Raspberry Pi for Omarchy

An Omarchy bar widget that shows the development boards plugged into your machine,
plus an MCP server that lets Claude Code actually talk to them.

Omarchy ships no serial support and no MCP servers, so this adds both:

- **In the bar** — connected Arduino / ESP32 / Pico boards, their ports, and whether
  they're accessible.
- **In Claude Code** — MCP tools for enumerating boards, reading and writing serial,
  compiling and flashing sketches, driving Raspberry Pi GPIO pins over SSH, and
  querying the [support matrix](docs/support-matrix.md).

## Requirements

| Dependency | Needed for | Installed by |
|---|---|---|
| Python 3.11+ | everything | already on Omarchy |
| `mcp`, `pyserial` | the MCP server | `setup.sh`, into `~/.local/share/omarchy-hardware/venv` |
| membership of `uucp` | serial access | `setup.sh` (`sudo usermod`) |
| `arduino-cli` | compiling and flashing | `setup.sh`, via `omarchy-mise-install` |
| `openssh` | Raspberry Pi GPIO | already on Omarchy |
| Claude Code | the MCP tools | already on Omarchy |

Serial monitoring and board listing work without `arduino-cli`. GPIO needs a Pi you can
already reach over SSH with key-based login. The Pi's host key must already be present
in `~/.ssh/known_hosts`; new keys are not accepted automatically.

## Install

```bash
omarchy plugin add https://github.com/sergiudanstan/omarchy-hardware --enable
~/.config/omarchy/plugins/io.github.sergiudanstan.hardware/bin/setup.sh
```

Then **log out and back in** — group membership only applies to new login sessions. The
panel will say so until you do.

`setup.sh` is idempotent; re-run it any time. `--check` prints the current state as JSON
without changing anything, `--dry-run` prints the commands that would run without
sudo/pip/file writes, and the panel's "Run setup" button just opens it in a terminal so
you can watch what it does.

## Remove

```bash
claude mcp remove omarchy-hardware
omarchy plugin remove io.github.sergiudanstan.hardware
rm -rf ~/.config/omarchy-hardware ~/.local/share/omarchy-hardware
```

Optionally `sudo gpasswd -d "$USER" uucp` to give up serial access again. Nothing else
is left behind — the plugin installs no udev rules, systemd units, or sudoers entries.

## What it can do

**Capabilities** — `list_capabilities` returns the per-family support matrix
(`supported`, `experimental`, or `unsupported`). Unimplemented operations use
the `UNSUPPORTED_OPERATION` error rather than a missing tool. See
[docs/support-matrix.md](docs/support-matrix.md).

**Lab report** — `hardware_report` combines local board/session state with the
support matrix and only reports the number of configured remote hosts; hostnames,
credentials, and private configuration values are not exported.

**Boards** — `list_boards`, `describe_board`

**Serial** — `serial_open`, `serial_status`, `serial_read`, `serial_write`,
`serial_query`, `serial_clear`, `serial_close`, `list_sessions`

The server holds ports open between tool calls and drains them into a 256 KB ring
buffer in the background, so reads never block on a silent device — every read has a
deadline, hard-capped at 10 seconds.

**Flashing** — `list_fqbns`, `compile_sketch`, `upload_sketch`

**Raspberry Pi GPIO** — `pi_status`, `gpio_list_pins`, `gpio_read_pin`, `gpio_set_mode`,
`gpio_write_pin`

**Raspberry Pi diagnostics** — `pi_inventory` reports bounded, read-only model,
OS, kernel, GPIO backend, temperature, and load information.

**Weintek HMI** — `weintek_opcua_read`, `weintek_opcua_write`,
`weintek_mqtt_publish`. OPC UA and MQTT only, exact allowlists, writes need
`confirm=true`. Live clients are not wired yet.

## Native development direction

Performance-sensitive hardware work is moving toward a native architecture:
C provides portable low-level parsing and device primitives, while C# provides
typed capability records, policy orchestration, and future adapters. The
`IHardwareAdapter` contract separates Raspberry Pi, Jetson, microcontroller,
Siemens, Schneider, Omron, and Weintek device families. The existing Python
MCP server remains the compatibility surface until the native backend has
equivalent tests and hardware validation.

Jetson support starts as read-only inventory and telemetry; it is deliberately
not treated as Raspberry Pi GPIO and is not enabled until a supported Jetson
device is physically validated.

Native microcontroller support starts with typed Arduino, ESP32, and RP2040
identity contracts and is designed to expand to Adafruit Feather, Teensy,
Seeed XIAO, STM32, M5Stack, micro:bit, nRF52, and other Arduino-like boards.
Serial writes and firmware flashing remain explicitly confirmed operations and
retain the existing board, FQBN, token, and audit checks.

Siemens LOGO!, S7-1200, Omron, and Schneider PLC support is currently a typed,
read-only-first contract only. Weintek cMT/MT HMI support is a separate family
whose intended transports are OPC UA and MQTT, with exact endpoint/node/topic
allowlists. Live clients are not enabled yet; the tools still refuse unlisted
targets. Writes need `confirm=true`. EasyAccess and project download are out
of scope.

## Configuration

`~/.config/omarchy-hardware/config.toml`, created by `setup.sh` with mode `600`:

```toml
[pi]
hosts = ["raspberrypi.local"]   # empty by default; GPIO tools are inert until set
allowed_pins = [2, 3, 4, ...]   # BCM 0 and 1 excluded (HAT ID EEPROM)
ssh_timeout = 10

[serial]
max_write_bytes = 4096
write_budget_bytes_per_min = 65536

[flash]
allow = true

# Weintek OPC UA / MQTT — off until allowlisted
# [weintek]
# allow = false
# [[weintek.opcua]]
# endpoint = "opc.tcp://192.168.1.50:4840"
# nodes = ["ns=2;s=Temperature"]
# [[weintek.mqtt]]
# host = "192.168.1.50"
# port = 1883
# topics = ["cMT/machine/temp"]
```

The file is refused if it's group- or world-readable, since it names the hosts the
plugin may reach.

## Security

Full detail: [SECURITY.md](SECURITY.md) for reporting a vulnerability, and
[docs/threat-model.md](docs/threat-model.md) for trust boundaries and accepted risks.

Omarchy plugins run **unsandboxed inside the shell process**, so it's fair to want to
know exactly what this one does. In full:

- **`sudo` is used once**, in `setup.sh`, for `usermod -aG uucp $USER`. Nothing else in
  the plugin ever escalates. The panel's setup button opens a terminal rather than a
  graphical polkit prompt, so you see the command and its output.
- **Serial paths are allowlisted.** Only `/dev/ttyACM*`, `/dev/ttyUSB*` and
  `/dev/serial/by-id/*` are accepted, and the path is re-checked *after* symlink
  resolution, so a symlinked device can't redirect a write to `/dev/sda`. `/dev/ttyS*`
  is deliberately excluded because those are often serial consoles.
- **There is no "run a command on the Pi" tool.** GPIO builds fixed `pinctrl` /
  `raspi-gpio` argument lists with `shell=False`. The host must be in your config
  allowlist and the pin must be in your allowed pin list.
- **Flashing is double-gated.** `upload_sketch` needs both a token minted by a
  successful `compile_sketch` in the same server process and an explicit `confirm=true`,
  and it refuses boards it cannot identify. Every upload is logged to
  `~/.local/state/omarchy-hardware/flash.log`; the upload is refused if the audit log
  cannot be written. The connected board must also be recognised and match the requested
  FQBN immediately before upload.
- **Writes are capped** per call and rate-limited per port.
- **The MCP server never runs as root** and never calls `sudo`.

Read `bin/setup.sh` before running it — it's commented for exactly that purpose.

## Status

The automated tests run without any hardware and cover board detection, the serial stack
(against PTY pairs), configuration validation, the path-allowlist layer, GPIO parsing and
upload-token binding.

**Verified end to end:** board discovery, serial sessions, `list_fqbns`, and
`compile_sketch` — compiling a real sketch through the MCP server produces a real
`.hex`. The upload gates are verified too: a genuine token is rejected for a different
sketch or board, and a valid token plus `confirm=true` still cannot reach a
non-allowlisted device.

**Not yet verified against physical hardware:** the final `upload_sketch` write to a
board, and Raspberry Pi GPIO against a real Pi. Treat those two as experimental and
report what breaks. Online simulators cannot stand in here — they never expose a local
`/dev/ttyACM*`, and a `socat` pseudo-terminal is correctly refused by the allowlist.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full setup and the boundaries a change must
not weaken.

```bash
~/.local/share/omarchy-hardware/venv/bin/python -m pytest mcp/tests/ -q   # no board needed
omarchy plugin validate .
omarchy-shell shell rescanPlugins
```

CI runs on every push: the test suite on Python 3.11-3.13, `ruff` (including the
flake8-bandit security ruleset), `shellcheck`, `pip-audit`, and `zizmor` to audit the
workflows. Actions are pinned to commit SHAs and dependencies are hash-pinned in
`mcp/requirements.lock`.

**QML is not statically linted.** `qmllint` needs Qt plus Quickshell's type registrations,
which a hosted runner does not have; `BoardsModel.js` is syntax-checked and `Panel.qml` is
reviewed by hand. Said plainly rather than implied otherwise.

This project targets the [OpenSSF OSPS Baseline](https://baseline.openssf.org/) **Level 1**.
It is not certified against any regulation, and makes no claim of NIS2 or Cyber Resilience Act
compliance -- see the closing section of [SECURITY.md](SECURITY.md).

## License

MIT — see [LICENSE](LICENSE).
