# Remediation Plan

This document tracks the security and reliability remediation work for
`omarchy-hardware`. It is intentionally versioned so that planning, model
interventions, implementation decisions, and validation remain visible in Git.

### 2026-09-22 — Claude: widget, scripts and docs review fixes

Branch: `agent/claude/widget-scripts-docs`. One of four independent PRs from a
full repository review the user requested. Panel: boards come before a
collapsed catalog; RP2 BOOTSEL and HID boards get their own status text; the
arrival grace is max(60 s, 3 x interval); duplicate serials are tracked;
arrivals use the filtered list; scan and status errors are shown correctly;
there is a flash row; the base Panel's IPC handler is used; dead helpers are
removed; parsers drop non-object entries. Doctor: `find_spec` (1.41 s down to
0.05 s, measured), at most once a minute from the timer, an rp2040-core check,
and `USER` unset is handled. Scripts keep stderr out of their JSON;
`hardware-mcp -B`; `test-native.sh` runs from any directory and CI uses it
with ASan/UBSan. Docs drift is fixed.

Validation: 721 Python tests (4 new; 3 of them fail against the old scripts),
21 widget tests (7 new, including a seeded fuzz of every parser), ShellCheck on
all of `bin/`, zizmor, Ruff on `.github/scripts` and `mcp/hardware_validation`, and
the native tests from another directory all passed locally. qmllint shows only
the unresolved `qs.*` import noise that main also has. The panel itself has
not been reloaded in the shell yet; that happens at deployment. Implementation
commit: `fix: widget layout, arrival tracking, doctor cost and docs drift`
(this entry is committed with the code).
### 2026-09-22 — Claude: network and bridge robustness review fixes

Branch: `agent/claude/network-robustness`. One of four independent PRs from a
full repository review the user requested. Slack: reply errors and malformed
events are logged instead of ending `serve()`, token settings are type-checked,
and handshake `OSError`s become `WsError` with the socket closed. MQTT: TLS
connect errors are wrapped and the TLS socket closed, `collect()` pings at
keepalive/2 (with `select`, so a packet is never abandoned mid-read), and parse
errors keep earlier messages. HTTP: `http.client.HTTPException` is wrapped, and
`_probe` isolates each target. InfluxDB: the error table is detected by its
header, `#` measurements are refused, and truncation is exact (the query asks for
limit + 1). Bridge: NaN is rejected, stop is checked before send, ports are
reserved, and the audit happens after the reservation. SSH: exit 255 is not
cached, and `pi_status` tolerates a missing pinctrl. Compute Module generations
are added in Python and C; C rejects NaN/inf. `run_uno.py` saves partial results.

Follow-up on the same branch: seeded stdlib fuzz tests for every parser of
device or network bytes (the Scorecard "Fuzzing" gap). They found `csv.Error`
escaping `ming.parse_csv` on a bare carriage return, now an `HttpError`.

Validation: 749 Python tests (32 new), Ruff, and the native C tests (also under
ASan/UBSan) passed locally. No broker, Slack workspace or Pi was used; the
fakes in `mcp/tests/ming_fakes.py` gained PINGREQ handling. Implementation
commit: `fix: keep Slack, MQTT, MING and SSH paths alive on network errors`
(this entry is committed with the code).
### 2026-09-22 — Claude: flash, Pico and budget review fixes

Branch: `agent/claude/flash-rp2-fixes`. One of four independent PRs from a full
repository review the user requested. RP2 identity: running boards are named by
USB product string, then by plain arduino-pico PID (`000a`, `f00a`, `000f`,
`f00f`, checked against the installed arduino-pico 6.1.0 `boards.txt` and
`USB.cpp`). `0009` and PIDs with HID bits set are not guessed, and a tty on `000f`
means a running Pico 2, not BOOTSEL. FQBN matching allows menu options but keeps
options the identification fixed. Upload audit records are always closed. UF2
copies use `O_NOFOLLOW` on FAT-only volumes, and a writeback error after the ROM
rebooted counts as flashed. Timeouts kill the uploader's process group. Budgets
keep their counts across limit edits. Also covered: the RP2040 boot-ROM serial
is untracked, the ESP32 MAC read happens only behind a UART bridge, uploads refuse
bridged ports, fingerprint resolves ports, dead sessions are cleared, and
`mpy_list` is out of the Slack observe tier.

Validation: 754 Python tests (37 new or updated) and Ruff passed locally. None of
this was run against a physical Pico, ESP32 or Nucleo. Implementation commit:
`fix: Pico identity, upload audit and flash budget review fixes` (this entry is
committed with the code).
### 2026-09-22 — Claude: config validation review fixes

Branch: `agent/claude/config-validation`. One of four independent PRs from a
full repository review the user requested ("review, fix issues, improve").
Config loading turns every malformed input into `ConfigError`: TOML syntax and
UTF-8 errors, non-table `pi`/`serial`/`flash` sections, and `ValueError` from
`urlparse`/`.port` in the OPC UA and HTTP URL checks. OPC UA security without a
pinned `trust_list` is refused at load time (policy.py already refused it per
call). `audit.verify` reports invalid UTF-8 as a chain break. The arrival helper
ignores a relative `OMARCHY_HARDWARE_ARDUINO_CLI`. A review suggestion to allow
an MQTT username without a password was not taken: the existing tests reject
"half a credential" on purpose.

Validation: 729 Python tests (12 new regression tests) and Ruff passed locally.
No hardware involved. Implementation commit: `fix: report malformed config as
config errors` (this entry is committed with the code).

### 2026-09-21 — Codex: Arduino MING tutorial and dashboard results

Branch: `agent/codex/ming-arduino-tutorial`. Document the physical Uno → USB
host adapter → MQTT/TLS → Node-RED → InfluxDB → Grafana workflow as a numbered
example, with a reusable validation runner and importable results dashboard.
Keep diagnostic echoes distinct from sensor readings, retain the failed rapid
run, and explain the existing flow's second-resolution limitation. No firmware,
plugin policy or Node-RED flow changes are part of this example.

Validation: the reusable runner passed 12/12 checks on the connected physical
Uno. Its generated dashboard was imported into Grafana, read back, and queried
for the same three values. Original rapid and paced runs plus the reusable
runner's evidence are included; no browser-rendering claim is made. All 717
Python tests, Ruff (including the new example), manifest/version/claims checks,
local Markdown links, failure/empty-run dashboard checks and diff checks passed.
CI now lints the example scripts. ShellCheck and remaining CI checks must be
green before merge. Implementation commit: `docs: add step-by-step Arduino MING
validation tutorial` (this entry is committed with the example).

### 2026-09-21 — Codex: physical Uno regression validation

Branch: `agent/codex/uno-validation-20260921`. The user connected an Arduino Uno
and authorized real-board tests followed by updating Git with the evidence.
Ran the existing `mcp/hardware_validation/run_uno.py` through the installed MCP
launcher at commit `e082968819751d1754962cc625864487ae5f9875`, after checking that
the validation sketch matched the repository and was inside the existing roots.
No flash-policy configuration was changed.

All 26 checks passed: discovery, redaction, compile, refusal gates, two physical
uploads (~3.8 s and ~3.7 s), serial banner/PING/echo/timeout, session restoration
and closing the final session. Added redacted JSON evidence and corrected the
stale threat-model claim that uploads had never been physically tested. Pico,
Pi, unplug/reconnect and board-swap checks remain explicitly unvalidated.
Implementation commit: `docs: record 26 passing Uno hardware checks` (this entry
is committed with the evidence).

### 2026-09-21 — Codex: finish Pico review cleanup and deployment

Branch: `agent/codex/pico-cleanup`. Remove the unused discovery-side
`RP2_BOARD_IDS` constant; upload validation uses `policy.UF2_BOARD_IDS`.
The user authorized fixing remaining review issues and updating the installed
plugin to the tested main revision, preserving the old deployed modifications.
Validation: 33 focused Pico/catalog tests, Ruff and diff checks passed. Full CI
must pass before merge and deployment.
Implementation commit: `chore: remove unused RP2 discovery constant` (this entry
is committed with the implementation).

### 2026-09-21 — Codex: complete Pico UF2 writes and select wireless variants

Branch: `agent/codex/pico-upload-completion`. User authorized commit, push and
merge. Preserve the deployed working tree separately; implement on current main.
Sync the UF2 destination before reporting success. Treat BOOTSEL IDs as chip
families and allow explicit Pico/Pico W or Pico 2/Pico 2 W selection within the
matching family, retaining the token, serial, artifact and confirmation gates.
Add regression coverage for variant selection, family mismatch and write errors.
No physical hardware validation is claimed.

Validation: 717 Python tests passed, including 64 focused Pico/preflight/catalog
tests. Ruff, native C, widget, manifest/version, documented-claims, shell syntax,
plugin validation on a copy of tracked files, and diff checks passed. ShellCheck
is delegated to CI because it is not installed locally. Implementation commit:
`fix: sync UF2 uploads and support Pico wireless variants` (this entry is
committed with the implementation).

### 2026-09-20 — Codex: Pico detection review follow-up

Branch: `agent/codex/pico-review-fixes`, retaining Grok's UF2 review fixes.
User authorized fixes, commit, push and merge after CI. Recognize RP2350
BOOTSEL PID `000f`, identify `000c` as a Debug Probe without a board FQBN,
and require a known board ID plus a HID interface for non-serial Pico discovery.
Add simulated USB regression tests; no physical flash is part of this change.
Validation: 708 Python tests passed outside the sandbox (5 existing Python 3.14
fork deprecation warnings); 127 focused tests, Ruff, native C, widget, shell
syntax, manifest/version, documented-claims and diff checks passed. The plugin
validator passed on a copy of tracked files (the checkout's ignored development
venv contains symlinks). ShellCheck is delegated to CI because it is not installed
locally. Implementation commit: `fix: distinguish Pico bootloaders and HID devices`
(this entry is committed with the implementation).

## Goals

- Prevent unintended SSH trust decisions and constrain remote targets.
- Reject unsafe or ambiguous configuration instead of coercing it.
- Make firmware flashing auditable and harder to direct at the wrong board.
- Eliminate silent failures in security-relevant logging.
- Validate destructive hardware operations on physical devices.

## Action plan

### P0 - SSH trust and scope

- [x] Replace `StrictHostKeyChecking=accept-new` with explicit host-key
      verification.
- [x] Decide whether approved host fingerprints should be supported in the
      configuration; if so, validate and document their format.
- [x] Validate configured host strings and reject control characters, whitespace,
      empty values, duplicates, and unsupported forms.
- [x] Add tests proving unknown or unapproved SSH targets are rejected.
- [x] Document the effect of `~/.ssh/config`, aliases, DNS, and host-key
      changes on the trust model.

### P1 - Configuration and auditability

- [x] Validate SSH timeout, serial write limits, and write budgets against
      positive bounded ranges.
- [x] Reject duplicate or invalid GPIO pins and require strict boolean values
      for flash configuration.
- [x] Replace silent upload-log failures with an explicit structured warning or
      error.
- [x] Ensure upload log directories and files are created with restrictive
      permissions.
- [x] Add tests for invalid configuration and unavailable or read-only audit
      logs.

### P1 - Flash safety

- [x] Bind the compile/upload authorization to the intended board identity when
      reliable identity data is available.
- [x] Re-enumerate and revalidate the board immediately before upload.
- [x] Test board replacement, disconnects, upload failures, and serial-session
      recovery.
- [x] Keep the short-lived compile token and explicit confirmation requirement.

### P1 - Physical validation

- [ ] Test serial discovery, read/write, and recovery with supported boards.
- [ ] Test real firmware upload on at least one supported board.
- [ ] Test Raspberry Pi status, pin reads, mode changes, and writes on a real
      Pi.
- [ ] Record hardware, software versions, commands, results, and limitations in
      `docs/hardware-validation.md`.

### P2 - Installation, CI, and documentation

- [x] Add a setup `--dry-run` mode.
- [x] Check required external commands before making changes.
- [x] Keep Python, shell, dependency, workflow, and manifest checks in CI.
- [x] Add regression tests for every remediation item.
- [x] Update the threat model, security policy, README, and changelog.
- [ ] Publish a release only after the relevant physical validation is complete.

### P1 - Firmware target and authorization integrity

- [x] Correct or disable the micro:bit v2 VID/PID mapping until its actual board
      identity and Arduino FQBN are unambiguous. Never describe the v1 target as
      a verified v2 mapping.
- [x] Bind an upload authorization to the exact successful build output, not
      only the sketch directory, FQBN, USB serial, and expiry. Upload that exact
      output and reject it if it has changed or is unavailable.
- [x] Add regression tests proving the ambiguous micro:bit mapping cannot pass
      flash preflight and a token cannot authorize a replaced or different build
      artifact. Keep board revalidation, explicit confirmation, and audit logging.

### P2 - Serial transaction reliability

- [x] Give serial writes a finite, documented timeout and translate timeout
      failures into the existing structured serial error response.
- [x] Serialize each `serial_query` clear/write/read transaction per session so
      concurrent calls cannot consume or discard each other's replies.
- [x] Add deterministic tests for a stalled write and overlapping queries; verify
      timeout recovery, reply ownership, and that subsequent calls still work.

### P3 - Bar widget visibility settings

- [x] Expose `showSupportedBoards` in `manifest.json` with a documented default.
- [x] Make the empty-board visibility behavior match the `showWhenNoBoards`
      setting, and test the combinations of connected boards, setup problems,
      `showWhenNoBoards`, and `showSupportedBoards`.

## Implementation order

1. SSH host-key verification and host validation.
2. Strict configuration validation.
3. Non-silent upload logging.
4. Board-bound upload authorization and pre-upload revalidation.
5. Unit and regression tests.
6. Physical hardware validation.
7. Documentation and release.

## Completion criteria

- Unknown SSH host keys are not accepted automatically.
- Invalid configuration is rejected with actionable errors.
- Every upload attempt is auditable or reports that audit logging failed.
- A board change between compilation and upload cannot silently redirect the
  operation.
- An upload token cannot authorize a build artifact other than the one it was
  minted for.
- Serial writes are time-bounded, and concurrent serial queries keep each reply
  paired with its request.
- Every bar-widget visibility option is represented in the manifest and behaves
  as documented.
- Serial and GPIO behavior has been verified on physical hardware.
- CI passes with regression coverage for each changed security boundary.

## Collaboration and intervention log

Record every meaningful intervention by a human or model in chronological order.
Each entry should reference the commit that contains the change.

| Date | Actor/model | Scope | Decision or change | Validation | Commit |
|------|-------------|-------|--------------------|------------|--------|
| 2026-09-11 | Copilot CLI | Initial review | Created this remediation plan from the repository security posture review. | Repository contents and threat model reviewed. | 1dfaa10 |
| 2026-09-11 | Copilot CLI | Implementation planning | Added the evidence-based action and implementation plan in `IMPLEMENTATION-PLAN.md`. | Current source, tests, setup script, CI, and contributor rules inspected; unresolved policy choices left explicit. | 954c6b7 |
| 2026-09-11 | Copilot CLI | P0/P1 implementation | Applied strict `known_hosts` SSH verification, conservative config validation, blocking audit-log preflight, restrictive audit permissions, and runtime board/FQBN revalidation. | 58 changed-area tests passed; Ruff, manifest, version, and diff checks passed. Full serial test run stalled in an existing PTY write test on macOS and was stopped. | e93fe7f |
| 2026-09-11 | Grok | F0–F4, F3, F5 template | Discarded unused `pi.host_keys` parser (trust stays in `known_hosts`). Removed dead `Config.pi_host_keys`. Serial write no longer `flush()`/`tcdrain()` (macOS PTY hang). Upload reports `session_restored`. SSH argv uses `--` before the host; `check_host` runs inside `_run`. SSH argv tests, upload preflight tests, `setup.sh --dry-run` + python3 preflight, portable plugin-dir resolution, Panel.qml quotes the setup path, `docs/hardware-validation.md` template. `--dry-run` is covered by pytest (`test_setup.py`); the token cannot push workflow-file edits. | 90 pytest passed on Darwin/py3.11; ruff passed; `bash -n` on all bin scripts; `setup.sh --dry-run` mutated nothing. `omarchy plugin validate` and `shellcheck` skipped (not on this Mac). | 2b632ef |
| 2026-09-11 | Grok | Support matrix | Added `docs/support-matrix.md`, `list_capabilities`, `UNSUPPORTED_OPERATION`, and `operations` on capability records. Pi GPIO/PWM/SPI/I2C, Jetson, and Siemens rows stay explicit; empty validation still means experimental. | 95 pytest passed; native C test passed; C# build skipped because no .NET SDK is installed. | Working tree integration |
| 2026-09-11 | Grok | Schneider + Weintek | Added `schneider` Modicon PLC and `weintek_hmi` families as unsupported typed contracts. No Modbus/UMAS/EasyAccess implementation. | pytest; C tests; ruff; CI green | d105f87 (#10) |
| 2026-09-11 | Grok | Weintek OPC UA/MQTT | Weintek transports are OPC UA and MQTT with exact endpoint/node/topic allowlists. Tools enforce the allowlist then return UNSUPPORTED_OPERATION until a live client is wired. | pytest; ruff; CI green | adce298 (#11) |
| 2026-09-12 | Grok | Contract revision | Aligned C# operation ids with the Python matrix, marked unimplemented ops unavailable, required GPIO confirm, redacted hardware_report serials, dropped Config.extra. | pytest; C tests; ruff; CI green | 185d11c (#12) |
| 2026-09-12 | Grok | Pi inventory | Extended `pi_inventory` with generation, tools, throttling, df, and operstate. Still read-only; no extra remote shell. | pytest; ruff; CI green | a281461 (#13) |
| 2026-09-12 | Grok | Security gates | Serial confirm, flash off by default with sketch_roots, USB-serial token binding, SSH forwarding/proxy pinned off, no host-allowlist leak, ambiguous Espressif PID unknown. Author Sergiu-Dan Stan. | 130 pytest; native C test; ruff; CI green | 73a177e (#14) |
| 2026-09-12 | Grok | Jetson inventory | Read-only `jetson_status`/`jetson_inventory` on `[jetson] hosts`. GPIO refuses Jetson allowlisted hosts. Extra Arduino/Pico W/XIAO USB IDs. | 142 pytest; native C test; ruff; CI green | fbdc138 (#15) |
| 2026-09-12 | Sergiu Dan Stan | Tool resolution | Restrict setup discovery to trusted system directories and default runtime tools to absolute paths. | pytest; ruff; bash -n; CI green | e189632 (#16) |
| 2026-09-12 | Antigravity | Arduino CLI path discovery | Check `OMARCHY_HARDWARE_ARDUINO_CLI` and `/usr/local/bin/arduino-cli` in `doctor.sh` and `setup.sh` to match `flash.py` runtime defaults. | doctor.sh; pytest; bash -n; ruff | af11d6c (#17) |
| 2026-09-12 | Antigravity | Board identification | Added 30 Arduino-like boards across Arduino, Raspberry Pi, Adafruit, Seeed, SparkFun, STM32, and micro:bit. | 150 pytest; ruff; bash -n | pending |
| 2026-09-12 | Copilot CLI | Scorecard dependency and SAST findings | Replaced direct CI/release pip installs with the hash-pinned CI lockfile and enabled CodeQL on pushes and pull requests. | 157 pytest; diff check | pending |
| 2026-09-12 | Codex | Whole-repository review plan | Recorded five follow-up issues: ambiguous micro:bit v2 firmware target, upload tokens not tied to build artifacts, unbounded serial writes, racy serial query transactions, and bar visibility settings that cannot be changed through the manifest. No implementation changes made. | Full Python suite (157 passed), native C tests, Ruff, manifest/version checks; ShellCheck and .NET SDK unavailable locally. | 1ccf1df |

| 2026-09-12 | Copilot CLI | Remediation implementation | Disabled ambiguous ST-LINK and micro:bit board claims, bound upload tokens to hashed compile artifacts, added bounded serial write timeouts and per-session query serialization, and exposed the supported-board visibility setting in the manifest. | Full Python suite: 157 passed. | 6e0dfa6 |
| 2026-09-12 | Composer | Security hardening | Fixed incomplete `compile_sketch` artifact minting, symlink-safe digests, resolved upload `--input-dir`, `O_NOFOLLOW` config open, and regression tests for replaced artifacts and micro:bit flash refusal. | pytest flash/config/upload suites green; ruff clean on touched files. | pending |
| 2026-09-18 | Claude (Opus 5) | Review fixes | Audit appends hold an inter-process `flock` and record `pid`; actuation outcomes (`_done`/`_failed`) logged; GPIO validated before budget/audit; `upload_sketch` restores `write_timeout_ms` and returns the new `session_id`; Weintek MQTT default port 8883; credential paths out of error messages. | 361 pytest; ruff; claims check; CI green | b590405 (#32) |
| 2026-09-18 | Claude (Opus 5) | Releases 0.1.2-0.1.4 | Changelog cut and duplicate headings merged; signed tags `v0.1.2`, `v0.1.3`, `v0.1.4`; release artifacts and attestations verified with `gh attestation verify`. | release.yml green; attestation verified | 2b07b79 (#33), bb773c2 (#40), 9c31cb4 (#42) |
| 2026-09-18 | Claude (Opus 5) | Weintek adapters | MQTT publish (#34) and subscribe (#37); Modbus TCP reads, standard library only, `allow_insecure` required (#38); OPC UA read/write via `asyncua` (LGPL-3.0+, hashed lock) with a required pinned server certificate (#39); `weintek_hmi_identify` from the standard Server object (#41). Experimental: in-process broker/Modbus/asyncua servers only, no physical cMT panel. | up to 387 pytest; pip-audit clean; CI green | b1f4396, e355234, 8471539, 1a77dc7, 7a003d4 |
| 2026-09-18 | Claude (Opus 5) | Panel and cleanup | Bar panel Targets section from offline `bin/panel-status.sh` (#36); tests linted in CI, `serial_query` `append_newline` (#35). Panel checked for QML errors in the live shell, not visually. | pytest; node widget tests; qmllint syntax; CI green | 7686c30 (#36), f21f132 (#35) |
| 2026-09-18 | Claude (Opus 5) | Process deviation | The branches for #32-#42 were named `feat/`, `fix/`, `chore/` and `release/` instead of `agent/claude/<topic>`, and this log was not updated in the same commits. Recorded here after the fact. `main` also advanced past `bcfb558` while marketplace verification #7254 was open; the reviewed commit is now pinned by the signed tag `marketplace-review-0.1.2`. | — | this PR |
| 2026-09-18 | Claude (Opus 5) | MING tutorial | Added `docs/ming-tutorial.md` for connecting an existing MQTT/InfluxDB/Node-RED/Grafana stack: least-privilege credentials per service, transport rules, `[ming]` config, `ming_status` troubleshooting, and enabling writes one at a time. Linked from the README and examples/ming-stack. | Example config parsed with `config._parse_ming`; error table checked against `config.py`, `http_lite.py` and `mqtt_lite.py` messages | this PR |
| 2026-09-19 | Claude (Opus 5) | Board profiles | Added `board_profile` and `hardware://board-profiles` resources backed by strictly validated TOML profiles (Uno R3, Nano, Mega 2560, ESP32-DevKitC, Pico, Pico W, Nucleo-64 F4, Pi 40-pin header). FQBN matching treats profile options as a subset, so a Nucleo keeps its `pnum`. `describe_board` returns `profile_id`. This is the first step of the "plug in, Claude proposes" plan. The pin data comes from manufacturer documents and was not physically validated. | 429 pytest; ruff; manifest, version and claims checks | this PR |
| 2026-09-19 | Claude (Opus 5) | Board journal | Per-board history keyed by a hash of USB vid:pid:serial: `upload_sketch` records successful flashes (last 20), `board_history`/`board_label` tools, label in `describe_board`. Locked read-modify-write for concurrent servers, `O_NOFOLLOW`, `0600`, corrupt entries set aside, labels restricted to a plain charset. A journal failure does not mask a successful upload. Stacked on the board-profiles PR. | 453 pytest; ruff; claims check | this PR |
| 2026-09-19 | Claude (Opus 5) | serial_expect | Line-based wait for a literal/prefix/JSON match on an open session, under the session's query lock, bounded to 30 s, 4 KiB lines and 20 context lines; output marked untrusted. No regex mode (uninterruptible backtracking). Bound under `serial.read` in the reference. Stacked on the board-journal PR. | 469 pytest (PTY); ruff; claims check | this PR |
| 2026-09-19 | Claude (Opus 5) | Parts inventory | `parts_inventory` over a user-written `parts.toml` (kinds, interfaces, 7-bit I2C addresses, bounded single-line text, 500-part and 256 KiB caps, `O_NOFOLLOW`, owner check); `examples/parts.toml`. Stacked on the serial_expect PR. | 487 pytest; ruff; claims check | this PR |
| 2026-09-19 | Claude (Opus 5) | Plug and propose | Widget arrival tracking (primed on first scan, 60 s re-enumeration grace, port regex before any command), notification with actions via `bin/board-arrived.sh`, per-board panel button and `bin/start-project.sh` creating a briefed project (CLAUDE.md with cleaned device strings, board.json without USB serial, hw-* commands, skill, `.mcp.json` only if unregistered) and opening Claude with the user's PATH. New scripts added to CI shellcheck. Not yet exercised in the live shell with a physical board. Stacked on the parts-inventory PR. | 518 pytest; 12 node widget tests; qmllint (no new syntax errors); ruff; claims check | this PR |
| 2026-09-19 | Claude (Opus 5) | Board fingerprint | `fingerprint_board` (3 s capture at 115200, literal matching of ESP ROM/MicroPython/CircuitPython/fw banners, untrusted sample). Opt-in `[flash] allow_fingerprinted` lets `upload_sketch` accept an unidentified adapter only for FQBNs a <5 min old ROM fingerprint of the same port and USB identity vouches for, audited before upload. Matrix row `board.fingerprint` (experimental, no validated board); C# ids aligned. Stacked on the plug-and-propose PR. | 530 pytest (PTY); ruff; claims check; C# build left to CI (no local SDK) | this PR |
| 2026-09-19 | Claude (Opus 5) | I2C probe | `peripherals.toml` (34 parts, ID registers from datasheets), `identify_i2c` with strict report parsing and parts cross-reference, read-only `omarchy_probe` sketch (repeated-start register reads only, table kept equal to the TOML by a test) copied into project folders, `/hw-probe` command. Probe compiled locally with arduino-cli for arduino:avr:uno, esp32:esp32:esp32, esp32:esp32:esp32s3, rp2040:rp2040:rpipico, Nucleo-F411RE and renesas_uno:minima; not run on hardware. Stacked on the fingerprint PR. | 548 pytest; ruff; claims check; 6 arduino-cli compiles | this PR |
| 2026-09-19 | Claude (Opus 5) | Wiring check | `wiring_check` (deterministic rules over the board profile: pin existence and aliases, reserved/caution, role capabilities, 5 V tolerance and down-level driving, open-drain I2C as warning, per-pin and total current, inductive loads, conflicts, I2C lines/pull-ups/address clashes); profile resolution shared with `board_profile`; `/hw-wire` runs it. Stacked on the I2C probe PR. | 574 pytest; ruff; claims check | this PR |
| 2026-09-19 | Claude (Opus 5) | Crash decoder | `upload_sketch` keeps the flashed ELF from the verified snapshot (journal, 0600, last 3 per board); `decode_crash` parses ESP32 Xtensa/RISC-V/abort reports (8-digit hex addresses only, 8 KiB input) and runs addr2line (chosen by ELF e_machine under ~/.arduino15) with a fixed argv, handling inlined frames via -a. Checked by hand against a real esp32:esp32:esp32 build with the installed toolchain. Stacked on the wiring-check PR. | 585 pytest (fake addr2line); ruff; claims check; manual decode with xtensa-esp32-elf-addr2line | this PR |
| 2026-09-19 | Claude (Opus 5) | Flash budget | Planned "dev lease" dropped: a lease file is forgeable by a model with same-user shell access, and consent already lives in Claude Code's permission prompt. Instead `policy.FlashBudget` (hourly, per board identity, charged before upload) with `[flash] max_uploads_per_hour`; `_RollingBudget` gains a window; CLAUDE.md states when Claude may flash without asking each time. Stacked on the crash-decoder PR. | 593 pytest; ruff; claims check | this PR |
| 2026-09-19 | Claude (Opus 5) | Serial→MQTT bridge | `bridge.py` thread per bridge (JSON objects only, re-serialised; min interval with newest-wins; MING write budget; size cap; auto-stop on duration ≤1 h or session close; ≤4 bridges, one per port); `serial_bridge_start/status/stop`; competing `serial_read`/`serial_expect` refused while bridged; audited start/stop; matrix row `ming.mqtt.bridge`; `/hw-dashboard`. No automatic Grafana dashboard creation (kept read/annotate only). Tested with PTY and a fake publisher, not against the local MING stack. Stacked on the flash-budget PR. | 600 pytest; ruff; claims check | this PR |
| 2026-09-19 | Claude (Opus 5) | Pi discovery | `pi_discover`: fixed-argv `avahi-browse -rtpk _ssh._tcp` parsed with host validation, `/proc/net/arp` matched against Raspberry Pi OUIs (vendor only, no MACs returned), `ssh-keygen -F` known-host check on validated names; never adds hosts or keys. Matrix row `pi.discover`; C# ids aligned. Ran live on this machine: no Pi on the network, empty result. Stacked on the bridge PR. | 605 pytest; ruff; claims check | this PR |
| 2026-09-19 | Claude (Opus 5) | Panel labels | Scan output carries the journal label (stdlib read, best effort); panel board names lead with it. Stacked on the Pi discovery PR. | 606 pytest; 13 node widget tests; scan run with /usr/bin/python3 | this PR |
| 2026-09-19 | Claude (Opus 5) | MicroPython | Raw-REPL client over SerialSession (new `transaction()` hook): `mpy_exec` (confirm, deadline with Ctrl-C and drain), `mpy_put` (confirm, 32 KiB, 3 KiB chunks wb/ab verified by os.stat size, audited once), `mpy_list`; JSON/base64 literal templates, path validation; serial port checks, byte budget, bridge exclusion. Matrix rows `micropython.*` with no validated board; C# ids aligned; skill mentions the MicroPython path. Tested against a fake raw-REPL board on a PTY, not a real one. Stacked on the panel-labels PR. | 621 pytest; ruff; claims check | this PR |
| 2026-09-19 | Claude (Opus 5) | ESP32 backup | `backup.py`: esptool 5 (`read-mac`, `read-flash --no-progress 0 ALL`, `write-flash 0x0`) with fixed argv, ESP-identified ports only, backups 0600 (last 3) with MAC/SHA-256 metadata; restore needs confirm, `[flash] allow`, flash budget, audit before/after, matching chip MAC and intact checksum. Output parsing checked against the esptool 5.3.1 source (`print_mac`, `Chip type:`), incl. EUI-64 chips. Tested with a fake esptool; no real ESP32 attached. Stacked on the MicroPython PR. | 629 pytest; ruff; claims check | this PR |
| 2026-09-19 | Claude (Opus 5) | Review fixes | Fixes six findings from an external review of the plug-and-propose stack plus five CodeQL notes: ESP32 backups keyed by chip MAC (per-chip retention; listing without touching the board); flash budget charged via a `before_write` hook after token/artifact/MAC checks (a test shows refused uploads never charge); `_require_unbridged` on write/query/clear; arrivals re-prime after a scan gap longer than the grace window; ELF chosen as `*.ino.elf` or a single ELF, with `elf_kept`/`elf_note` in the upload result; addr2line timeout and OSError mapped to tool errors; CodeQL empty-except and unnecessary-lambda notes. | 635 pytest; 14 node widget tests; ruff; claims check | this PR |
| 2026-09-19 | Grok | Flash budget follow-up | Two leftovers from that review: the port is closed only after `FlashBudget.charge` accepts the write, so a `RATE_LIMITED` Arduino upload leaves the caller's serial session in place; ESP32 still closes first to `read-mac` and returns the restored `session_id` with the error. Uploads and restores of an ESP32 share one hourly cap keyed by factory MAC (`_flash_budget_key`), so CP210x `0001` clones no longer share an upload allowance and a restore is not a second cap. | 639 pytest; ruff; claims check | this PR |
| 2026-09-19 | Claude (Opus 5) | Slack bridge | Claude Tag runs in Anthropic's cloud and cannot reach local USB or the stdio MCP server, so a local Socket Mode bridge: `ws_lite` (stdlib RFC 6455 client, wss only, bounded, unmasked-server-frame and mid-frame-stall checks), `slack_bridge` (deny-by-default channels/people, read/observe tiers derived from tool annotations with a test that no destructive or additive tool is reachable, `claude -p --restricted --tools "" --strict-mcp-config` with allow/deny lists, dontAsk and no prompts, budget and time caps, stdin prompt, escaped replies, dedupe, per-person hourly budget, single worker with a bounded queue, audited with request digests). One real headless run against the MCP server answered from `list_boards` and refused a shell request ($0.03). Not yet connected to a real Slack workspace. | 672 pytest; ruff; claims check; one real `claude -p` run | this PR |
| 2026-09-19 | Grok | Slack bridge onto main | Rebased the Slack bridge onto main after the flash-budget follow-up. A full request queue no longer spends the per-person hourly cap (the only enqueuer checks `full()` before `charge`). CodeQL: empty excepts commented, token-permission test no longer chmod 0644, runner default is None so the `run_claude` call is not treated as a bound method. Test count 682. | 682 pytest; ruff; claims check | this PR |
| 2026-09-20 | Grok | RP2040/Pico discovery | `list_boards` and the bar scan now include Pico boards that have no tty: BOOTSEL (`2e8a:0003`, mounted `RPI-RP2` UF2 volume) and HID-only Raspberry Pi USB devices. `upload_sketch` copies a compiled `.uf2` onto a volume that `policy.resolve_uf2_volume` has checked (`INFO_UF2.TXT` Board-ID RPI-RP2/RP2350, mount under `/run/media`, `/media` or `/mnt`). Arduino-pico HID+CDC `2e8a:000b` is identified as a Pico. Serial-port tools still go through `resolve_port` only. Exercised on a physical RP2040 (BOOTSEL volume + CDC after flash); not a hardware-validation row. | 690 pytest; ruff; claims check | this PR |
| 2026-09-20 | Grok | Pico UF2 review fixes | Three defects from the PR #65 review: `/proc/mounts` paths decoded with kernel octal escapes only (Unicode BOOTSEL mounts listed); unique UF2 selected before `before_write`/`upload_started` (missing/ambiguous UF2 does not charge the budget or close a session); `INFO_UF2.TXT` accepted only on an exact `Board-ID` of RPI-RP2 / RPI-RP2350 / RP2350. Serial upload path unchanged. No physical flash in this change. | 697 pytest; ruff; claims check | this PR |

### 2026-09-12 — Codex: MHS preparation

Branch: `agent/codex/mhs-preparation`. Implementation commit: `3aef7b5`.
Prepare a project-owned capability reference, expose it through existing MCP
and a local metadata CLI, and document the future official MHS adapter boundary.
Keep compatibility unverified and preserve current execution gates.

Validation: seven new reference tests cover policy refresh, private-field
exclusion, family support/bindings, invalid configuration, CLI output, and a real
stdio MCP exchange. The stdio test passes outside the Codex sandbox; inside it,
initialization times out. No hardware operations were invoked.
The remaining 156 Python tests pass in the sandbox. Ruff, native C tests,
manifest/version validation, and `git diff --check` pass as well.

### 2026-09-12 — Codex: MHS preview application draft

Added the official access-application link and reusable project summary to
`docs/mhs-readiness.md` for PR #18. Submission remains pending; no contact
details or hardware ownership claims were invented. This entry is part of the
documentation commit titled `docs: add MHS preview application draft`.
Validation: reviewed against the prepared application text and checked with
`git diff --check`; no runtime behavior changed.

### 2026-09-12 — Codex: hash-lock CI tooling

Branch: `agent/codex/hash-lock-ci-tools`. Added `.github/requirements-ci.in`
and a uv-generated universal hash lock for Python 3.11+, then routed CI,
security audit, and release tooling installs through that lock. Enabled CodeQL
on pushes and pull requests as well as its weekly schedule.

Validation: installed all 57 applicable pinned tools in a clean Python 3.11
environment; all 157 Python tests passed; Ruff, ShellCheck-equivalent bash
syntax, native C, manifest/version, zizmor, pip-audit, distribution build,
SBOM generation, and `git diff --check` passed. The initial in-sandbox stdio
test timed out due to restricted process pipes; the full suite passed outside
the sandbox. Commit hash is recorded by the commit on this branch.

## Change protocol for multiple models

1. Read this document before modifying the repository.
2. Add an entry to the collaboration log describing the intended change.
3. Implement only the scoped change and update the relevant checklist item.
4. Run the smallest applicable existing validation commands.
5. Record the validation result and commit hash in the log.
6. Push the commit so subsequent models can inspect the complete history.

### 2026-09-13 — Codex: repository review fixes

Branch: `agent/codex/review-fixes`. User authorized implementation, commit,
push, and merge after CI. Address all seven review findings: upload from a
verified private snapshot, discard in-flight serial input before queries,
recover failed sessions, redact stable USB paths, propagate Jetson SSH failures,
derive the widget catalog from board definitions (including Arduino Uno), and
honor empty-widget visibility. Add focused regression coverage and run the
full applicable checks before merging. No physical hardware validation claimed.

Validation: 180 Python tests passed outside the sandbox, including MCP stdio;
3 widget tests, native C tests, Ruff, manifest/version checks, Omarchy plugin
validation, shell syntax checks, scanner/catalog smoke check, and diff checks
passed. The sandbox run hit the documented MCP initialization timeout;
ShellCheck is delegated to CI. Implementation commit: `85b99b148e87cc9f0a4ce01be2790e8663afe493`.

CI follow-up for PR #21: all four Python versions, native, shell, widget and
security-audit jobs passed. CodeQL identified three issues in new test code
(side effects in an assertion, exception-path reachability, and a redundant
lambda). Separate the serial write from its assertion, express cleanup testing
with an explicit exception handler, and use the config factory directly.

Follow-up validation: all 52 affected Python tests, Ruff, and diff checks passed.
