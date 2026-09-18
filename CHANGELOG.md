# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security
- Every operation that changes physical state is now recorded before it happens,
  in `~/.local/state/omarchy-hardware/audit.log`. Serial writes, GPIO mode and
  level changes and firmware uploads are refused outright if the record cannot be
  written. Records are chained with SHA-256 and the new `audit_status` tool
  reports the first line that no longer follows its predecessor, so a history
  rewritten by anything running as the user is visible rather than silent.
  Previously only flashing was audited, to `flash.log`.
- OPC UA and MQTT targets carry a transport security context, defaulting to
  `Basic256Sha256` / `SignAndEncrypt` with a client certificate, and to TLS on
  port 8883. Reaching an endpoint unsigned, unencrypted or in cleartext now
  requires `allow_insecure = true` on that exact target. `policy.check_weintek_*`
  return the validated target, so no code path can produce an authorised endpoint
  without the settings needed to reach it safely. The same shape is mirrored in
  the .NET adapter contracts, which previously could not express an authenticated
  session at all. The tools themselves remain unimplemented.
- GPIO writes are rate-limited per host and pin (`[pi] actuation_budget_per_min`,
  default 120). Only serial traffic was capped before.
- `bin/scan-boards.sh` pins `PATH` and runs `/usr/bin/python3` with `-s`, matching
  `setup.sh` and `doctor.sh`. The bar widget runs it unattended every few seconds,
  and it was the one script that still resolved its interpreter through the user's
  environment.
- FQBNs are validated against a format before reaching `arduino-cli` argv, rather
  than relying on that parser to reject a value beginning with `-`.
- Unexpected exceptions no longer return their message to the model; the type is
  reported and the detail goes to stderr. Exception text routinely carries the
  absolute paths and hostnames that `hardware_report` deliberately redacts.

### Added
- MING stack tools, for the MQTT/InfluxDB/Node-RED/Grafana setup a Pi or lab
  machine usually publishes into, on this machine or a remote one: `ming_status`,
  `mqtt_subscribe`, `mqtt_publish`, `influx_measurements`, `influx_query`,
  `influx_write`, `nodered_flows`, `nodered_inject`, `grafana_dashboards` and
  `grafana_annotate`, configured under `[ming]` with named targets. Topics,
  buckets and inject nodes are allowlisted; subscribe filters can be narrowed
  but not widened. InfluxDB is queried through typed parameters, never raw Flux,
  and written through escaped line protocol. Node-RED flows can be read and
  inject nodes triggered, but not deployed. Writes need `confirm=true`, count
  against `write_budget_per_min` and are audited before they are sent. The MQTT
  3.1.1 and HTTP clients use only the standard library, so no dependency was
  added. Experimental: tested against in-process fakes and run end to end
  against `examples/ming-stack`. Support-matrix family `ming_stack`.
- `examples/ming-stack`: a Docker Compose file running Mosquitto, InfluxDB,
  Node-RED and Grafana with TLS, digest-pinned images and a bootstrap script
  that writes the matching `config.toml` block. Claude's credentials are scoped
  on the services too: a Mosquitto ACL, bucket-scoped InfluxDB tokens, a Grafana
  Viewer service account, and a Node-RED token that can read flows and press
  inject buttons but not deploy. Its CA is name-constrained to the stack's own
  names and addresses, so it can be trusted in a browser safely.
- `examples/ming-stack/demo`: a greenhouse demo for Omarchy. A simulated device,
  a Node-RED bridge into InfluxDB, a provisioned Grafana dashboard, desktop
  notifications, and a walkthrough of Claude cooling the greenhouse with the
  plugin's tools.
- MQTT passwords can come from a mode-600 file (`password_file`) as well as an
  environment variable, since Claude Code starts the MCP server and an exported
  variable has to reach its environment.
- `audit_status` MCP tool: verifies the audit log's hash chain and names the first
  record that does not follow.
- CI builds the .NET adapter contracts, which shipped in the plugin but were never
  compiled; audits the CI toolchain's own dependencies for known vulnerabilities;
  and verifies that the countable claims in `docs/threat-model.md` still match the
  repository, so the evidence offered to third parties cannot quietly go stale.
- Releases attest an archive of the plugin tree, not only the sdist and wheel. The
  marketplace installs this repository, so provenance for the wheel alone covered
  an artifact nobody runs.
- Physical validation on an Arduino Uno through the MCP server: discovery, serial
  sessions, compile, upload with token + confirm, and upload refusal paths (26/26
  checks). Results in `docs/hardware-validation.md`, repeatable with
  `mcp/hardware_validation/run_uno.py`.
- `supported_boards` on support-matrix rows lists the exact FQBNs an operation was
  physically validated on. All six microcontroller operations list
  `arduino:avr:uno`; the family stays experimental for other boards.
- STM32 in the hardware check: `hardware_report` lists `stm32_usb_devices`
  (ST-LINK V1/V2 probes and the DFU bootloader, which have no serial port), more
  ST-LINK V2-1/V3 and STM32 USB CDC IDs are named, and `doctor.sh` reports a
  missing `STMicroelectronics:stm32` core or probe udev access while an STM32
  device is connected.
- Nucleo boards are identified from their ST-LINK drive label (`NOD_F411RE`),
  giving a named board and an `STMicroelectronics:stm32` FQBN for 24 listed
  Nucleo-32/64/144 parts; other Nucleo labels are named without an FQBN.
- `examples/stm32-servo-sweep`: continuous servo sweep on a Nucleo-F411RE (D3),
  clamped to 0..50 degrees and attached at 0 so it never passes the 90 degree
  default.

### Fixed
- `docs/threat-model.md` claimed `analyze python` was a required status check on
  `main` when it was not, reported a test count that had been stale for months,
  described the OpenSSF Scorecard alerts as dismissed when they are open, and
  listed the Weintek allowlists as an active control although the tools return
  `UNSUPPORTED_OPERATION` before reaching them. All four are corrected, and
  CodeQL and the native core are now required checks.
- A comment in `gpio_ssh.py` claimed `ssh` was resolved through `PATH`; it has
  been an enforced absolute path since 0.1.1.
- Upload a verified private copy of compile artifacts so concurrent rebuilds cannot
  replace firmware after verification; remove the copy after success or failure.
- Drain and discard in-flight serial input before queries, serialize other reads,
  writes and clears with queries, and preserve ordinary read deadlines.
- Mark failed serial readers closed and let `serial_open` reconnect after a disconnect.
- Remove serial-bearing USB by-id paths from redacted hardware reports.
- Report Jetson SSH failures instead of declaring an unreachable host healthy.
- Populate the widget catalog from identified board targets, including Arduino Uno;
  omit ambiguous ST-LINK and micro:bit targets.
- Honor `showWhenNoBoards` independently of supported-catalog display.
- Install the MCP server with `--no-build-isolation` in setup and CI, and pin the
  `setuptools` build backend with hashes in `mcp/requirements.lock`, so installing no
  longer downloads an unpinned build backend from PyPI.
- Make `setup.sh` and `doctor.sh` check `arduino-cli` and `ssh` at the exact absolute
  paths the MCP server executes instead of any copy on `PATH`, and reject relative
  overrides the same way the server does.
- Stop the missing-`arduino-cli` error from telling users that `setup.sh` installs it.
- Correct stale `setup.sh` header and dry-run text left over from removed install steps.
- Address CodeQL findings: explain intentionally ignored load-average parse errors,
  move a side-effecting call out of a test `assert`, and test group- and
  world-accessible config rejection through `load()` without creating a group- or
  world-accessible file.

### Added
- Project-owned hardware capability reference available through the read-only
  `get_hardware_reference` MCP tool and `python -m omarchy_hardware.reference`.
  Includes existing operation bindings and a redacted policy snapshot, plus an
  MHS preparation plan; does not claim Model Hardware Standard compatibility.
- Remove automatic Claude MCP registration and unpinned executable installation;
  setup now leaves agent configuration and third-party tool installation to the user.
- Restrict setup discovery to system tool directories and use fixed absolute
  defaults for Python, sudo, usermod, SSH, and Arduino CLI.
- Add `bin/test-native.sh`, a repeatable C11 validation command used by CI.
- Read-only `jetson_status` and `jetson_inventory` on a separate `[jetson] hosts`
  allowlist. No GPIO, no `pinctrl`.
- Add USB identification and FQBN mappings for 30 additional Arduino-compatible boards
  including Arduino UNO R4 WiFi, Nano ESP32, GIGA R1, Portenta H7, MKR family, Raspberry Pi
  Pico 2 (RP2350), Adafruit Feather/QT Py/Trinket/CircuitPlayground, Seeed XIAO RP2040/nRF52840,
  SparkFun Pro Micro, STM32 Nucleo, and BBC micro:bit v2.
- USB IDs for Arduino Due/Zero/MKR/UNO R4 Minima, Pico W, and Seeed XIAO SAMD21.
  PJRC Teensy is named as a vendor but not flashable.

### Security
- Finish compile→upload artifact binding: `compile_sketch` digests the reported
  build directory, mints a token for that path+digest, and refuses missing
  arduino-cli build output. Uploads resolve the artifact path, reject symlink
  trees, and pass only the resolved directory to `arduino-cli`.
- Open `config.toml` with `O_NOFOLLOW` and validate permissions on the open fd
  to close the symlink TOCTOU window.
- Require `confirm=true` for `serial_write` and `serial_query`; refuse writes to
  unidentified adapters unless `[serial] allow_unknown = true`.
- Default `[flash] allow` to false and require `sketch_roots` when flashing is enabled.
- Bind upload tokens to USB serial when sysfs reports one, and encode token fields
  as JSON so `|` cannot collide.
- Pin SSH `ForwardAgent`, `ForwardX11`, `PermitLocalCommand`, port forwarding, and
  `ProxyCommand` off; do not list `[pi] hosts` in tool errors.
- Treat Espressif `303a:1001` as an unknown adapter (S2/S3 share that PID).
- Refuse config files that are symlinks or owned by another user; create config and
  the flash log with mode `0600` from the start.

### Changed
- Reopening a serial port at a different baud is an error until the session is closed.
- GPIO `read_pin` accepts stdout only for the requested BCM number.
- Weintek tools return `UNSUPPORTED_OPERATION` until a live client is wired, without
  revealing allowlist membership.
- C# adapters require an identity allowlist before talking to a remote endpoint.
- CI installs runtime deps from the hash lockfile and runs the native C test.
- Align C# adapter operation ids with the Python support matrix, mark
  unimplemented industrial and Jetson operations unavailable, and require
  `confirm=true` for GPIO mode changes and writes.
- Deduplicate the support matrix, drop leftover Weintek generic HMI/PLC rows,
  redact board serials in `hardware_report`, and stop storing the raw config
  table on `Config`.
- Expand `pi_inventory` with Pi generation, tool presence, throttling flags,
  root filesystem usage, and eth0/wlan0/end0 operstate.
- Add a redacted read-only `hardware_report` for local lab state and capability
  reporting without exporting remote hostnames or credentials.
- Add a hardware support matrix and `list_capabilities` so each family reports
  `supported`, `experimental`, or `unsupported` operations, with
  `UNSUPPORTED_OPERATION` for work that is not implemented.
- Add `pi_inventory`, a bounded read-only Raspberry Pi capability report for
  model, OS, kernel, GPIO backend, temperature, and load.
- Add a typed capability record for hardware adapters.
- Add a C native-core foundation and C# orchestration project for the staged
  migration away from Python in performance-sensitive paths.
- Add typed C# adapter contracts with explicit read-only, state-changing, and
  destructive operation classes.
- Add a read-only C# Raspberry Pi adapter contract for bounded model, OS,
  kernel, thermal, and load diagnostics.
- Add a separate read-only Jetson adapter contract for Orin-oriented inventory
  and telemetry paths; it does not assume Raspberry Pi GPIO compatibility.
- Add native microcontroller identity and operation contracts for Arduino,
  ESP32, and RP2040 workflows while preserving existing flash gates.
- Add read-only-first Siemens LOGO! and S7-1200 PLC identity/tag contracts with
  explicit destructive-operation classification.
- Add Schneider Modicon PLC and Weintek HMI families to the support matrix as
  unsupported until typed, allowlisted adapters exist.
- Make OPC UA and MQTT the Weintek HMI transports, with exact endpoint/node/
  topic allowlists, `weintek_opcua_read` / `weintek_opcua_write` /
  `weintek_mqtt_publish` tools, and no live client until a later PR.

### Security
- Require existing SSH host keys instead of accepting new keys automatically.
- Reject unsafe hardware configuration values and custom GPIO allowlists that
  include BCM 0 or 1.
- Require a writable, fsynced flash audit log before starting an upload.
- Recheck the connected board's suggested FQBN before flashing.

### Fixed
- Prevent serial writes from hanging indefinitely on PTYs or disconnected
  adapters by avoiding an unbounded POSIX `tcdrain()` call.
- Reject SSH hosts that start with `-` or contain `/`, so a configured destination
  cannot be parsed as an ssh option or a path.
- Re-check the GPIO host allowlist immediately before constructing the ssh argv.

### Fixed
- Place `--` *before* the SSH destination. OpenSSH treats `--` after the host as
  the first word of the remote command, which meant GPIO tools could never run
  `pinctrl` or `raspi-gpio` on a real Pi.
- Stop calling `Serial.flush()` after writes. On POSIX that is `tcdrain()`, which
  can block indefinitely on PTYs and some disconnected adapters.
- Close a dead serial session before opening the same port again.
- Report serial-session restore failures after an upload instead of swallowing them.
- Quote the setup script path when the panel launches a terminal or editor.

### Added
- `setup.sh --dry-run` prints the commands that would run and makes no changes.
  Setup also checks for `python3` (and `sudo` when a group change is needed)
  before mutating anything.
- Helper scripts resolve their plugin directory without GNU `readlink -f`.
- Regression tests for SSH argv construction, unapproved hosts, upload preflight
  (unknown/replaced/disconnected boards, confirmation, session restore), and
  setup `--dry-run`.
- `docs/hardware-validation.md` — a log to fill in when physical boards and a
  Pi are actually tested. Empty on purpose until then.

## [0.1.1] - 2026-09-11

### Fixed
- **The `pinctrl` output parser was broken for almost every real format.** It assumed at most
  one optional pull token, so `26: ip -- | lo`, `26: op -- -- | lo`, `6: op dl pu | lo` and
  `17: op dh | hi` were all silently skipped — only the `a3 pu` shape parsed. On a real Pi,
  `gpio_list_pins` would have returned little or nothing and `gpio_read_pin` would have failed
  outright. The parser now reads the whole flag run, and also reports drive state (`dh`/`dl`).

### Added
- Upload-token regression tests: the token is bound to the exact sketch, board and expiry it
  was minted for, and a forged signature or extended expiry is rejected.
- GPIO parser regression tests covering the real `pinctrl` and `raspi-gpio` output formats
  (52 tests total, none requiring hardware).

### Changed
- Documentation now distinguishes what is actually verified. `compile_sketch` is verified end
  to end through the MCP server, producing a real `.hex`; only the final `upload_sketch` write
  to a board and Pi GPIO remain unverified against physical hardware.

## [0.1.0] - 2026-09-10

First release.

### Added

**The plugin**
- Quickshell bar widget listing connected USB development boards, with a panel showing
  each board's port, identity and accessibility, and a banner reporting outstanding setup.
- MCP server exposing 18 tools to Claude Code.
- Board discovery via sysfs with VID/PID identification for Arduino, ESP32, RP2040 and
  common USB-serial bridge chips.
- Serial sessions owned by the server process and drained by a background thread into a
  bounded ring buffer, so a read never blocks indefinitely on a silent device.
- Sketch compiling and flashing through `arduino-cli`, gated by a compile-minted HMAC
  token plus explicit confirmation.
- Raspberry Pi GPIO over SSH using fixed `pinctrl`/`raspi-gpio` argv against host and pin
  allowlists. No tool runs arbitrary commands on the Pi.
- `bin/setup.sh`: one-time, idempotent, user-run setup with a `--check` mode.

**Assurance**
- 26 tests requiring no hardware, using PTY pairs, including adversarial path-escape cases.
- CI on every push: the suite on Python 3.11–3.13 (3.14 non-blocking), `shellcheck`,
  manifest validation mirroring Omarchy's own validator, a symlink guard, and a check that
  the three version strings agree.
- Security tooling: `ruff` with the flake8-bandit ruleset, `pip-audit` against the
  lockfile, and `zizmor` auditing the workflows. CodeQL and OpenSSF Scorecard weekly.
- `mcp/requirements.lock`: 29 packages pinned with hashes, covering all transitives.
- GitHub Actions pinned to full commit SHAs; workflow tokens default to `contents: read`
  and no checkout persists credentials.
- `SECURITY.md` with a private reporting channel, response targets and explicit scope;
  `docs/threat-model.md`; `CONTRIBUTING.md`.
- Release artifacts carry build provenance and a CycloneDX SBOM.

### Known limitations
- Flashing and Raspberry Pi GPIO are implemented and guarded but have **not** been verified
  against physical hardware.
- QML is not statically linted in CI: `qmllint` needs Qt plus Quickshell type registrations
  that hosted runners do not provide. `BoardsModel.js` is syntax-checked instead.
- Single maintainer, so OpenSSF Scorecard's Code-Review and Contributors checks cannot pass.

[Unreleased]: https://github.com/sergiudanstan/omarchy-hardware/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/sergiudanstan/omarchy-hardware/releases/tag/v0.1.1
[0.1.0]: https://github.com/sergiudanstan/omarchy-hardware/releases/tag/v0.1.0
