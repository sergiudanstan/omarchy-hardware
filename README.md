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
| `arduino-cli` | compiling and flashing | installed separately by the user |
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

`setup.sh` does not modify Claude Code or install third-party executables. Install a
trusted, pinned `arduino-cli` release separately if compiling or flashing is needed.
Runtime SSH defaults to `/usr/bin/ssh` and Arduino CLI defaults to
`/usr/local/bin/arduino-cli`; overrides must be absolute paths. `setup.sh` and the panel
check those exact paths.
`setup.sh` installs Python dependencies, including the `setuptools` build backend, only
from the hash-pinned `mcp/requirements.lock`, then installs the server with
`--no-build-isolation` so pip fetches nothing unpinned.
`setup.sh` is idempotent; re-run it any time. `--check` prints the current state as JSON
without changing anything, `--dry-run` prints the commands that would run without
sudo/pip/file writes, and the panel's "Run setup" button just opens it in a terminal so
you can watch what it does.

## Remove

```bash
claude mcp remove omarchy-hardware
omarchy plugin remove io.github.sergiudanstan.hardware
rm -rf ~/.config/omarchy-hardware ~/.local/share/omarchy-hardware ~/.local/state/omarchy-hardware
```

Optionally `sudo gpasswd -d "$USER" uucp` to give up serial access again. Nothing else
is left behind — the plugin installs no udev rules, systemd units, or sudoers entries.

## What it can do

**Plug in, and Claude proposes something to build.** When a board appears, a
notification names it: its label, if it has one, what was last flashed and when,
and whether it has a pin profile. The notification has two buttons:
- **Start with Claude** creates `~/Projects/hw/<board>-<time>/` and opens Claude
  Code in it with `CLAUDE.md` (board facts, the rules for wiring and flashing),
  `board.json`, and the `/hw-propose`, `/hw-wire` and `/hw-build` commands.
  Claude proposes projects from your parts, plans and checks the wiring, writes
  firmware that reports on itself, and asks before every flash.
- **Serial monitor** opens `arduino-cli monitor`, when arduino-cli is installed.

The same "Start with Claude" button sits next to each board in the panel. Boards
that were already plugged in when the shell started are not announced, and
neither is a board that drops off and comes back within a minute (a reset or a
reflash). Turn the notification off with the `notifyOnArrival` setting.
`OMARCHY_HARDWARE_PROJECTS` and `OMARCHY_HARDWARE_CLAUDE` (absolute paths)
override the projects folder and the Claude binary. For uploads from those
folders to work, add the projects folder to `[flash] sketch_roots`.

**Capabilities** — `list_capabilities` returns the per-family support matrix
(`supported`, `experimental`, or `unsupported`). Unimplemented operations use
the `UNSUPPORTED_OPERATION` error rather than a missing tool. See
[docs/support-matrix.md](docs/support-matrix.md).

**Lab report** — `hardware_report` combines local board/session state with the
support matrix and only reports the number of configured remote hosts; hostnames,
credentials, and private configuration values are not exported.

**Model Hardware Standard preparation** — `get_hardware_reference` describes
family capabilities, existing MCP tool bindings, and a redacted policy snapshot
without contacting hardware. The same reference is available as JSON through
`python -m omarchy_hardware.reference` in the plugin environment. This is a
project-owned format; MHS compatibility is not yet implemented. See
[MHS readiness](docs/mhs-readiness.md) for Claude setup, validation, and the
work that depends on the official specification.

**Boards** — `list_boards`, `describe_board`

Arduino Uno is identified as `arduino:avr:uno`. The widget's supported-board
catalog comes from the same detection table; ambiguous adapters are excluded.
`showSupportedBoards` controls the catalog inside the panel. With no visible
boards, `showWhenNoBoards` controls the bar icon unless setup needs attention.

**Board profiles** — `board_profile` returns a board's pinout, logic voltage,
5 V tolerance, per-pin current limits, reserved pins (such as the ESP32 flash pins)
and caution pins (strapping pins, USB serial pins). It looks the board up by
connected port, FQBN or profile id, and with no arguments lists the profiles. The
same data is readable as MCP resources at `hardware://board-profiles/{id}`, and
`describe_board` names the matching `profile_id`. The profiles are Uno R3, Nano,
Mega 2560, ESP32-DevKitC, Pico, Pico W, Nucleo-64 F401RE/F411RE/F446RE and the
Raspberry Pi 40-pin header. They are written from manufacturer documents, not
physically validated. Add a board by adding a TOML file in
`mcp/omarchy_hardware/profiles/`; the loader rejects unknown keys and capabilities.

**What's wired to it** — every project folder gets `omarchy_probe/`, a
read-only I2C probe sketch. It compiles for AVR, ESP32, ESP32-S3, RP2040,
STM32 Nucleo and UNO R4. It scans the default bus and reads chip-ID registers,
setting the register pointer with a repeated start and never writing a data
byte. It then prints one JSON report every 5 seconds. `/hw-probe` walks
through flashing it (with confirmation, since it replaces the firmware) and
reading the report. `identify_i2c` names what answered, from a table of 34
common parts (BME/BMP280, MPU-6050/9250, SSD1306, PCF8574 LCD backpacks,
VL53L0X, ADS1115 and more). A chip ID that matches makes a part confirmed; an
address alone makes it possible. Matches are cross-checked against your
parts.toml.

**Board fingerprint** — `fingerprint_board` identifies boards that USB can't name
because they sit behind a generic CH340 or CP210x chip. It opens the port at
115200 baud for 3 seconds, which resets most boards, and matches what they print:
- ESP32 boot ROM banners (ESP32, S2, S3, C3, C6) give the chip, the FQBN and,
  for a classic ESP32, the DevKitC profile;
- MicroPython and CircuitPython banners give the runtime, board and MCU;
- the `{"fw": ...}` line of firmware built with `/hw-build` gives the sketch name.

Matching uses literal text, and the sample lines are marked untrusted. Flashing
such a board stays refused unless you set `[flash] allow_fingerprinted = true`.
Even then, the FQBN must be one the ROM banner vouches for, and the fingerprint
must be under 5 minutes old and taken from the same port and USB identity. The
relaxed check is written to the audit log before the upload starts.

**Wiring check** — `wiring_check` tests a wiring plan against the board profile
in code, not by the model's judgement. Each connection gives a pin, part and
role, and optionally the part's voltage, the load, the current, whether a driver
switches it, its I2C address and pull-ups. The check flags:
- pins that don't exist, reserved pins, and caution pins;
- missing capabilities (PWM, analog, DAC, input-only pins);
- 5 V logic on boards that aren't 5 V tolerant, and a 5 V board driving a
  3.3 V part;
- current per pin and in total;
- relays, motors and solenoids without a driver, and servo supply advice;
- pin conflicts, a missing SDA or SCL, I2C without pull-ups, and I2C address
  clashes.

The verdict is `fail`, `check` or `pass`. `/hw-wire` runs it before any code is
written.

**Crash decoder** — `upload_sketch` keeps a private copy of the ELF it just
flashed, from the verified snapshot, keeping the last 3 per board. When an ESP32
panics, `decode_crash` takes the report lines (`Guru Meditation Error`, Xtensa
`Backtrace:`, RISC-V `MEPC`/`RA`, or `abort() was called at PC`) and pulls out
only the hex addresses. It picks addr2line from the installed Arduino cores by
the ELF's machine type and returns the function, file and line, including
inlined callers. It was checked against a real ESP32 build: a store through a
null pointer decoded to `explode()` at `crashy.ino:4`, called from `loop()`.

**Board journal** — each board is recognised by its USB vendor, product and serial
number, so its history follows it to any port. `upload_sketch` records every
successful flash: time, FQBN, sketch folder and artifact digest, keeping the last
20. `board_history` returns them newest first. `board_label` gives the board a name
such as `greenhouse-node`, and `describe_board` shows it. Boards with no USB serial
number, which covers most CH340 clones, get no history rather than a shared one.
Entries live in `~/.local/state/omarchy-hardware/boards/` (mode `0600`). Each
file name is a hash, because the serial comes from the device. A label is 1–40
letters, digits, spaces, dots, underscores or hyphens, because it is read back to
the model later.

**Parts inventory** — `parts_inventory` reads `~/.config/omarchy-hardware/parts.toml`,
which you write, so Claude can propose projects built from what you already have.
You can filter by `kind` or `interface`. Start from
[`examples/parts.toml`](examples/parts.toml). The file is validated strictly
(known kinds and interfaces, 7-bit I2C addresses, bounded one-line text, at most
500 parts). A missing file isn't an error.

**Serial** — `serial_open`, `serial_status`, `serial_read`, `serial_expect`, `serial_write`,
`serial_query`, `serial_clear`, `serial_close`, `list_sessions`

The server holds ports open between tool calls and drains them into a 256 KB ring
buffer in the background, so reads never block on a silent device — every read has a
deadline, hard-capped at 10 seconds. `serial_write` and `serial_query` require
`confirm=true`. Writes to unidentified adapters also need `[serial] allow_unknown = true`.
Reopening a port at a different baud is refused until the session is closed.
Queries discard pending input in both the reader and OS buffer before sending;
other serial reads, writes, and clears cannot consume or interrupt that query.
Serial transport cannot distinguish a late device response that arrives after a
new command; protocols needing that guarantee must include request IDs.
Disconnected sessions report closed and can be reopened with `serial_open`.
`serial_expect` waits, for up to 30 seconds, for a line that contains some text
(`literal`), starts with it (`prefix`), or is a JSON object with given keys and
values (`json`, for example `{"selftest": true}`). It returns the line plus up to
20 lines of context, and marks the result `untrusted`. It has no regular-expression
mode on purpose: a backtracking pattern cannot be interrupted and would freeze the
server.

**Flashing** — `list_fqbns`, `compile_sketch`, `upload_sketch`

**Raspberry Pi GPIO** — `pi_status`, `gpio_list_pins`, `gpio_read_pin`, `gpio_set_mode`,
`gpio_write_pin`. Mode changes and writes require `confirm=true`.

**Raspberry Pi diagnostics** — `pi_inventory` reports bounded, read-only model,
generation (Pi 3/4/5), OS, kernel, GPIO backend, tool presence, temperature,
load, `vcgencmd` throttling, root filesystem usage, and eth0/wlan0/end0
operstate. GPIO writes still need `confirm=true`.

**Jetson** — `jetson_status`, `jetson_inventory`. Separate `[jetson] hosts`
allowlist. Read-only model/L4T/thermal/storage. No GPIO.

**Weintek HMI** — `weintek_hmi_identify`, `weintek_opcua_read`, `weintek_opcua_write`,
`weintek_mqtt_publish`, `weintek_mqtt_subscribe`, `weintek_modbus_read`. OPC UA and MQTT only, exact allowlists, writes need
`confirm=true`. OPC UA read/write (signed and encrypted, pinned HMI certificate), MQTT
publish/subscribe (TLS) and Modbus TCP reads are live and experimental.

**MING stack** (MQTT, InfluxDB, Node-RED, Grafana; local or remote) —
`ming_status`, `mqtt_subscribe`, `mqtt_publish`, `influx_measurements`,
`influx_query`, `influx_write`, `nodered_flows`, `nodered_inject`,
`grafana_dashboards`, `grafana_annotate`. Targets are named in `config.toml`
and every topic, bucket and inject node is allowlisted. InfluxDB queries are
built from typed parameters, never raw Flux. Node-RED flows can be read and
inject nodes triggered, but not deployed, since a flow can run arbitrary code.
Writes need `confirm=true`, count against `[ming] write_budget_per_min`, and are
audited. [`docs/ming-tutorial.md`](docs/ming-tutorial.md) connects an existing stack step by
step; [`examples/ming-stack`](examples/ming-stack) runs the whole stack in
Docker with TLS for a Pi or a lab machine.

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
Serial writes and firmware flashing are explicitly confirmed operations and
retain the existing board, FQBN, USB-serial token, sketch-root, and audit checks.

Siemens LOGO!, S7-1200, Omron, and Schneider PLC support is currently a typed,
read-only-first contract only. Weintek cMT/MT HMI support is a separate family
whose intended transports are OPC UA and MQTT, with exact endpoint/node/topic
allowlists. OPC UA, MQTT and Modbus (read-only) are live and experimental.
Unlisted targets are refused. Writes need `confirm=true`. EasyAccess and project download are out
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
write_timeout_ms = 2000
write_budget_bytes_per_min = 65536
allow_unknown = false

[flash]
allow = false
# sketch_roots = ["~/Arduino"]   # required when allow = true

[jetson]
hosts = []   # empty by default; Jetson tools are inert until set

# Weintek OPC UA / MQTT — off until allowlisted
# [weintek]
# allow = false
# [[weintek.opcua]]
# endpoint = "opc.tcp://192.168.1.50:4840"
# nodes = ["ns=2;s=Temperature"]
# [[weintek.mqtt]]
# host = "192.168.1.50"
# port = 8883
# topics = ["cMT/machine/temp"]
# [[weintek.modbus]]                # EasyBuilder Pro MODBUS Server driver; read-only
# host = "192.168.1.50"
# allow_insecure = true             # Modbus TCP has no authentication or encryption
# read = ["LW-100:16", "LB-0:32"]

# MING stack — off until allowed; see setup.sh's template for every key
# [ming]
# allow = true
# [[ming.mqtt]]
# name = "pi"
# host = "pi.local"                  # TLS on 8883 by default
# subscribe = ["sensors/#"]
# publish = ["actuators/fan"]
# security = { ca_file = "~/.config/omarchy-hardware/ming-ca.pem", username = "claude", password_file = "~/.config/omarchy-hardware/mqtt-password" }
# [[ming.influxdb]]
# name = "pi"
# url = "https://pi.local:8086"
# org = "home"
# read_buckets = ["sensors"]
# security = { ca_file = "~/.config/omarchy-hardware/ming-ca.pem", token_file = "~/.config/omarchy-hardware/influxdb-token" }
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
  allowlist and the pin must be in your allowed pin list. SSH also pins
  `ForwardAgent=no`, `ForwardX11=no`, `PermitLocalCommand=no`,
  `ClearAllForwardings=yes`, and `ProxyCommand=none` so `~/.ssh/config` cannot
  forward your agent or run a local command. Error text does not list allowlisted
  hostnames.
- **Flashing is double-gated and off by default.** Set `[flash] allow = true` and
  `sketch_roots`. `upload_sketch` needs a token minted by a successful
  `compile_sketch` in the same server process, `confirm=true`, a recognised board
  whose FQBN matches, and (when sysfs has one) the same USB serial the token was
  minted for. Upload verifies a private copy of the build directory and uses that
  copy throughout the operation, then removes it.
- **Serial writes are confirmed.** `serial_write` / `serial_query` need
  `confirm=true`. Unidentified adapters are refused unless `[serial] allow_unknown`.
- **Writes are capped** per call and rate-limited per port. GPIO has its own
  budget counted in operations per pin, because what wears a relay is the number
  of transitions, not the amount of data. Both follow config changes without
  restarting the server.
- **Everything that moves hardware is logged first**, to
  `~/.local/state/omarchy-hardware/audit.log` (mode `0600`): serial writes, GPIO
  mode and level changes, and firmware uploads. If the record cannot be written,
  the operation is refused rather than performed unrecorded. Each line is chained
  to the one before it with SHA-256, so `audit_status` reports a rewritten or
  truncated history instead of accepting it. Payloads are recorded by length and
  digest, never content — enough to confirm "this exact command was sent" without
  keeping a plaintext copy of everything your devices received.
- **Industrial endpoints are secure by default.** OPC UA targets sign and encrypt
  with a client certificate, MQTT targets use TLS. Reaching an HMI without that is
  possible, but needs `allow_insecure = true` on that exact target, so it is a
  decision someone made rather than one nobody noticed.
- **`confirm=true` is a second tool call, not a human dialog.** Clients that honour
  `destructive_hint` can still prompt you. A steered model can pass `confirm=true`
  on its own.
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

**Verified on an Arduino Uno (2026-09-16):** discovery, serial open/read/write/query/close,
compile, and the final `upload_sketch` write, all driven through the MCP server over stdio.
The refusal paths were checked on the same board: no `confirm`, wrong FQBN, forged token,
changed artifact digest, and a sketch outside `sketch_roots`. 26 of 26 checks passed; see
[docs/hardware-validation.md](docs/hardware-validation.md).

**Not yet verified against physical hardware:** unplug/reconnect, uploads to other board
families, and Raspberry Pi GPIO against a real Pi. The MING tools have run end to end
against `examples/ming-stack` on x86_64, but not yet against a stack on a Pi. Treat those as experimental and
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
