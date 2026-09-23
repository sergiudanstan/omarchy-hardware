# Test results

Physical and compliance test results for **omarchy-hardware v0.1.5** (release commit
`e04f705`), recorded on **2026-09-22**. Every tool ran through the installed plugin's MCP
server over stdio, against a real Arduino Uno. Nothing here is simulated.

| Suite | Result | What it covers | Evidence |
|---|---|---|---|
| Arduino Uno — baseline | **26/26** | Discovery, redaction, compile, every flashing gate, two real uploads, serial round-trips | [uno-2026-09-22.json](docs/hardware-validation/uno-2026-09-22.json) |
| Arduino Uno — extended + MING | **31/31** | Profiles, wiring, labels, I2C probe, refusals, journal, audit pairing, serial→MQTT bridge | [uno-extended-2026-09-22.json](docs/hardware-validation/uno-extended-2026-09-22.json) |
| NIS2 technical evidence | **38 pass · 0 fail · 4 limitation · 2 n/a** | Article 21(2)(a)–(j) and Article 23 | [nis2-2026-09-22.json](docs/hardware-validation/nis2-2026-09-22.json) |
| MING stack (Mosquitto, InfluxDB, Node-RED, Grafana) — 2026-09-23 | **24/24** | Stack hardening, broker auth and ACLs, all ten MING tools, end-to-end data and command paths | [ming-2026-09-23.json](docs/hardware-validation/ming-2026-09-23.json) |
| Automated tests (CI) | **804 passed** at the time of this run | Unit, integration and fuzz tests; no hardware | CI on every pull request |

A first extended run scored 30/31. The single failure was in the test runner (it expected labels from `list_boards`, which in v0.1.5 did not carry them; it does since), not in the plugin. The check was corrected and the suite re-run; that run is kept as [evidence](docs/hardware-validation/uno-extended-2026-09-22-first-run.json).

## Test setup

| | |
|---|---|
| Board | Arduino Uno, USB `2341:0043`, `/dev/ttyACM0` |
| Host | Omarchy, kernel 7.2.5-3-omarchy, Python 3.14.7 |
| Toolchain | arduino-cli  Version: 1.4.1 |
| MING stack | Local `examples/ming-stack`: Mosquitto (TLS), InfluxDB, Node-RED, Grafana |
| Runners | [`run_uno.py`](mcp/hardware_validation/run_uno.py), [`run_uno_extended.py`](mcp/hardware_validation/run_uno_extended.py), [`run_nis2.py`](mcp/hardware_validation/run_nis2.py) |

## Arduino Uno — baseline (26/26)

| Check | Tool | Result |
|---|---|---|
| Server starts over stdio and lists tools | `list_tools` | ✅ pass |
| Discovery finds the Uno with FQBN arduino:avr:uno | `list_boards` | ✅ pass |
| Non-board serial ports are not listed | `list_boards` | ✅ pass |
| describe_board reports the Uno and no open session | `describe_board` | ✅ pass |
| hardware_report redacts the USB serial | `hardware_report` | ✅ pass |
| list_fqbns includes arduino:avr:uno | `list_fqbns` | ✅ pass |
| serial_open on a non-allowlisted port is refused | `serial_open` | ✅ pass |
| compile_sketch outside sketch_roots is refused | `compile_sketch` | ✅ pass |
| compile_sketch produces an artifact and upload token | `compile_sketch` | ✅ pass |
| upload_sketch without confirm is refused | `upload_sketch` | ✅ pass |
| upload_sketch with a different FQBN is refused | `upload_sketch` | ✅ pass |
| upload_sketch with a forged token is refused | `upload_sketch` | ✅ pass |
| upload_sketch with a different artifact digest is refused | `upload_sketch` | ✅ pass |
| upload_sketch with token + confirm flashes the Uno | `upload_sketch` | ✅ pass |
| serial_open at 115200 | `serial_open` | ✅ pass |
| serial_open is idempotent at the same baud | `serial_open` | ✅ pass |
| serial_open at a different baud is refused | `serial_open` | ✅ pass |
| serial_read receives the sketch banner after reset | `serial_read` | ✅ pass |
| serial_write without confirm is refused | `serial_write` | ✅ pass |
| serial_query PING returns PONG | `serial_query` | ✅ pass |
| serial_write then serial_read round-trips through the board | `serial_write/serial_read` | ✅ pass |
| serial_status reports no errors | `serial_status` | ✅ pass |
| serial_read on a silent device times out instead of hanging | `serial_read` | ✅ pass |
| upload_sketch with a session open closes and restores it | `upload_sketch` | ✅ pass |
| Restored session reads the banner after re-flash | `serial_read` | ✅ pass |
| serial_close releases the port | `serial_close` | ✅ pass |

## Arduino Uno — extended + MING (31/31)

| Check | Tool | Result |
|---|---|---|
| Exactly one Uno is connected | `list_boards` | ✅ pass |
| describe_board maps the Uno to the arduino_uno_r3 profile | `describe_board` | ✅ pass |
| board_profile returns the Uno pin map | `board_profile` | ✅ pass |
| get_hardware_reference describes the families | `get_hardware_reference` | ✅ pass |
| list_capabilities answers | `list_capabilities` | ✅ pass |
| parts_inventory answers (an empty inventory is valid) | `parts_inventory` | ✅ pass |
| wiring_check accepts an LED on D13 | `wiring_check` | ✅ pass |
| wiring_check fails a motor driven straight from a pin | `wiring_check` | ✅ pass |
| board_label is stored and read back by describe_board and board_history | `board_label` | ✅ pass |
| firmware_backup is refused on a non-ESP32 board | `firmware_backup` | ✅ pass |
| decode_crash rejects text that is not a crash report | `decode_crash` | ✅ pass |
| upload_sketch to a port that is not there says not connected | `upload_sketch` | ✅ pass |
| compile and flash the read-only I2C bench probe | `compile_sketch + upload_sketch` | ✅ pass |
| serial_expect catches the probe report | `serial_expect` | ✅ pass |
| identify_i2c accepts the probe's device list | `identify_i2c` | ✅ pass |
| serial_expect times out cleanly when nothing matches | `serial_expect` | ✅ pass |
| serial_clear, list_sessions and serial_status agree on one open session | `serial_clear/list_sessions/serial_status` | ✅ pass |
| mpy_list fails cleanly on an Arduino (no MicroPython) | `mpy_list` | ✅ pass |
| ming_status reaches the MQTT broker | `ming_status` | ✅ pass |
| serial_bridge_start forwards the session to MQTT | `serial_bridge_start` | ✅ pass |
| A bridged session's input is refused to serial_read | `serial_read` | ✅ pass |
| mqtt_subscribe receives the probe's JSON from the bridge | `mqtt_subscribe` | ✅ pass |
| serial_bridge_status counts what it published | `serial_bridge_status` | ✅ pass |
| serial_bridge_stop ends the bridge and keeps the session | `serial_bridge_stop` | ✅ pass |
| fingerprint_board recognises the probe's firmware banner | `fingerprint_board` | ✅ pass |
| compile and flash the validation sketch again | `compile_sketch + upload_sketch` | ✅ pass |
| The board is back on the validation sketch and answers PING | `serial_read + serial_query` | ✅ pass |
| board_history records this run's two uploads | `board_history` | ✅ pass |
| The board's original label is restored | `board_label` | ✅ pass |
| Audit chain intact; every upload_started has its upload_finished | `audit_status` | ✅ pass |
| The bridge's start and stop are both in the audit log | `audit log` | ✅ pass |

### What crossed MQTT

> **Diagnostic probe output, not sensor readings.** The Uno ran the read-only I2C bench probe; nothing is wired to it, so the bus is empty. 6 messages arrived on `actuators/uno-probe` in 15 s:

```json
{"probe":"done","bus":"Wire","i2c":[]}
{"selftest":true,"i2c_count":0}
{"probe":"done","bus":"Wire","i2c":[]}
{"selftest":true,"i2c_count":0}
{"probe":"done","bus":"Wire","i2c":[]}
{"selftest":true,"i2c_count":0}
```

## NIS2 evidence (38 pass · 0 fail · 4 limitation · 2 n/a)

NIS2 (EU 2022/2555) obliges organisations, not products. These results are technical evidence an organisation using this plugin can cite for its own Article 21 measures. **They are not a certification.** Scope and mapping: [docs/nis2.md](docs/nis2.md).

| ID | Article | Control | Result |
|---|---|---|---|
| NIS2-A-1 | 21(2)(a) | Documented threat model and security policy | ✅ pass |
| NIS2-A-2 | 21(2)(a) | Threat-model claims match the code | ✅ pass |
| NIS2-B-1 | 21(2)(b) | Hash-chained audit log verifies end to end | ✅ pass |
| NIS2-B-2 | 21(2)(b) | An edited record is detected at its line | ✅ pass |
| NIS2-B-3 | 21(2)(b), 23 | Actuations are logged with time, process and outcome | ✅ pass |
| NIS2-B-4 | 21(2)(b) | Private vulnerability reporting with response targets | ✅ pass |
| NIS2-B-5 | 23 | The audit log holds only real events | ⚠️ limitation |
| NIS2-C-1 | 21(2)(c) | What was flashed to each board is recorded | ✅ pass |
| NIS2-C-2 | 21(2)(c) | The audit chain continues across server restarts | ✅ pass |
| NIS2-C-3 | 21(2)(c) | Firmware can be backed up and restored (ESP32) | ✅ pass |
| NIS2-C-4 | 21(2)(c) | config.toml backup | ⚠️ limitation |
| NIS2-D-1 | 21(2)(d) | Every runtime dependency is version-pinned and hash-locked | ✅ pass |
| NIS2-D-2 | 21(2)(d), (e) | No known vulnerabilities in the locked dependencies | ✅ pass |
| NIS2-D-3 | 21(2)(d) | CI actions are pinned to commit SHAs | ✅ pass |
| NIS2-D-4 | 21(2)(d), (e) | Dependency updates are automated | ✅ pass |
| NIS2-D-5 | 21(2)(d) | The release ships a CycloneDX SBOM | ✅ pass |
| NIS2-D-6 | 21(2)(d) | Release artifacts carry verifiable build provenance | ✅ pass |
| NIS2-E-1 | 21(2)(e) | main requires signed commits, CI and resolved reviews | ✅ pass |
| NIS2-E-2 | 21(2)(e) | The release tag is signed | ✅ pass |
| NIS2-E-3 | 21(2)(e) | No open CodeQL findings | ✅ pass |
| NIS2-E-4 | 21(2)(e) | CI is green on main | ✅ pass |
| NIS2-E-5 | 21(2)(e) | Coordinated vulnerability disclosure policy | ✅ pass |
| NIS2-E-6 | 21(2)(e), (f) | OpenSSF Scorecard items | ⚠️ limitation |
| NIS2-F-1 | 21(2)(f) | The automated test suite passes | ✅ pass |
| NIS2-F-2 | 21(2)(f) | Physical validation on real hardware passes | ✅ pass |
| NIS2-G-1 | 21(2)(g) | config.toml is private, owned and not a symlink | ✅ pass |
| NIS2-G-2 | 21(2)(g) | A readable or symlinked config is refused | ✅ pass |
| NIS2-G-3 | 21(2)(g) | Credential files are private | ✅ pass |
| NIS2-G-4 | 21(2)(g) | No secrets stored inline in config.toml | ✅ pass |
| NIS2-H-1 | 21(2)(h) | Upload authorisation is an HMAC that cannot be forged or reused | ✅ pass |
| NIS2-H-2 | 21(2)(h), (j) | MQTT over TLS with the pinned CA connects | ✅ pass |
| NIS2-H-3 | 21(2)(h), (j) | TLS refuses an unknown CA and a wrong hostname | ✅ pass |
| NIS2-H-4 | 21(2)(h), (j) | HTTPS to InfluxDB is verified against the pinned CA | ✅ pass |
| NIS2-H-5 | 21(2)(h) | TLS 1.2 is the minimum protocol version | ✅ pass |
| NIS2-I-1 | 21(2)(i) | Only allowlisted serial devices can be opened | ✅ pass |
| NIS2-I-2 | 21(2)(i) | Compile and upload stay inside sketch_roots | ✅ pass |
| NIS2-I-3 | 21(2)(i) | Remote hosts outside the allowlist are refused | ✅ pass |
| NIS2-I-4 | 21(2)(i) | Writes are rate-limited per target | ✅ pass |
| NIS2-I-5 | 21(2)(i) | Confirmation gates and serial redaction hold on the real board | ✅ pass |
| NIS2-I-6 | 21(2)(i) | Connected hardware assets are inventoried | ✅ pass |
| NIS2-I-7 | 21(2)(i) | Human-resources security | ➖ n/a |
| NIS2-J-1 | 21(2)(j) | Cleartext MQTT to a remote host needs an explicit waiver | ✅ pass |
| NIS2-J-2 | 21(2)(j) | The maintainer's GitHub account uses 2FA | ⚠️ limitation |
| NIS2-J-3 | 21(2)(j) | MFA for the local MCP server | ➖ n/a |

### Limitations, stated

- **NIS2-B-5 — The audit log holds only real events.** A unit test wrote 31 fake `firmware_restore_failed` records into the real log between 2026-09-19 and the isolation fix (#76). They stay, because removing them would break the hash chain, and they are identifiable by backup id `20260919-120000-aaaaaaaaaaaa`.
- **NIS2-C-4 — config.toml backup.** There is no built-in config backup. The file is small and user-owned; back it up with the rest of `~/.config`.
- **NIS2-E-6 — OpenSSF Scorecard items.** Open items: CII Best Practices, Code-Review, Fuzzing, Maintained. A single-maintainer project cannot pass Code-Review or Maintained; see [docs/threat-model.md](docs/threat-model.md).
- **NIS2-I-7 — Human-resources security.** Organisational measure; a desktop plugin has no staff processes.
- **NIS2-J-2 — The maintainer's GitHub account uses 2FA.** GitHub reports 2FA only to a token with the user scope; this token has repo, workflow, read:org and gist. Confirm it under GitHub Settings → Password and authentication.
- **NIS2-J-3 — MFA for the local MCP server.** Runs as the logged-in user over stdio; there is no network login to protect.

## MING stack — full check (2026-09-23)

The example stack in [`examples/ming-stack`](examples/ming-stack) (Mosquitto 2.0.22, InfluxDB 2.9.1,
Node-RED 5.0.7, Grafana 13.2.2, all pinned by digest) with the simulated greenhouse, checked by
[`run_ming.py`](mcp/hardware_validation/run_ming.py) at three layers: the stack itself, the services'
own authentication and ACLs, and all ten plugin MING tools through the real MCP server.

Plugin under test: the installed plugin at `ad45528` (`main`, after v0.1.5 and review pass 2).

**24/24 passed** ([evidence](docs/hardware-validation/ming-2026-09-23.json)).
The first run found 3 real problems, fixed in the same change and re-run
([evidence of the first run](docs/hardware-validation/ming-2026-09-23-before-fixes.json)):

| Found | Fix |
|---|---|
| The CA private key was readable inside the Node-RED, InfluxDB and Mosquitto containers (`certs/` is mounted into every service; Node-RED runs as the host user's uid). That CA is trusted by the browser for `localhost`. | The key moved to `ca/`, which no container mounts; `bootstrap.sh` migrates existing stacks. |
| Node-RED's admin password, API token and credential secret were visible through `docker inspect`. | Node-RED reads them from files mounted into `/run/secrets`, like the other services. |
| A device could inject arbitrary InfluxDB points through the demo flow: a payload `21.5\n<measurement> value=1` wrote a second measurement into `sensors`. | The flow validates the field name and the value (`on`, `off` or a plain number) and rejects anything else. |

The first run's MING-I-4 failure was the runner misreading OpenSSL's output for a refused
handshake; all four services refuse TLS 1.1.

| ID | Layer | Check | Result |
|---|---|---|---|
| MING-I-1 | Stack | All five services are running | ✅ pass |
| MING-I-2 | Stack | Published ports bind to loopback only | ✅ pass |
| MING-I-3 | Stack | Images are pinned by digest and the running images match | ✅ pass |
| MING-I-4 | Stack | TLS 1.1 refused; 1.2 and 1.3 verified against the stack CA | ✅ pass |
| MING-I-5 | Stack | No container can read the CA private key | ✅ pass |
| MING-I-6 | Stack | No secret value is passed in a container's environment | ✅ pass |
| MING-I-7 | Stack | Keys and secrets are not world-readable on the host | ✅ pass |
| MING-M-1 | Services | Mosquitto refuses anonymous and wrong-password clients | ✅ pass |
| MING-M-2 | Services | Broker ACL: devices cannot command actuators; claude cannot fake readings | ✅ pass |
| MING-T-1 | Plugin tool | ming_status reaches all four services | ✅ pass |
| MING-T-2 | Plugin tool | mqtt_subscribe receives the greenhouse's temperature and humidity | ✅ pass |
| MING-T-3 | Plugin tool | MQTT refusals: allowlist, wildcards, filter width, confirmation | ✅ pass |
| MING-T-4 | Plugin tool | mqtt_publish actuators/fan=on is obeyed by the greenhouse | ✅ pass |
| MING-T-5 | Plugin tool | influx_measurements lists greenhouse in the sensors bucket | ✅ pass |
| MING-T-6 | Plugin tool | influx_query returns greenhouse temperatures from the last 2 minutes | ✅ pass |
| MING-T-7 | Plugin tool | influx_write to the claude bucket reads back | ✅ pass |
| MING-T-8 | Plugin tool | InfluxDB refusals: bucket scope and line-protocol comments | ✅ pass |
| MING-T-9 | Plugin tool | nodered_flows shows the greenhouse flow and its inject nodes | ✅ pass |
| MING-T-10 | Plugin tool | nodered_inject fan0off switches the greenhouse fan off | ✅ pass |
| MING-T-11 | Plugin tool | nodered_inject refuses a node outside inject_nodes | ✅ pass |
| MING-T-12 | Plugin tool | grafana_dashboards lists the provisioned greenhouse dashboard | ✅ pass |
| MING-T-13 | Plugin tool | grafana_annotate writes an annotation | ✅ pass |
| MING-E-1 | End to end | A device cannot inject extra InfluxDB points through the demo flow | ✅ pass |
| MING-E-2 | Services | Admin APIs refuse anonymous access; the claude token cannot deploy flows | ✅ pass |

> **Simulated demo readings, not sensor measurements.** `demo/greenhouse.sh` generates the greenhouse values.
> A sample received through `mqtt_subscribe`:

```
sensors/greenhouse/fan = off
sensors/greenhouse/temperature = 25.99
sensors/greenhouse/humidity = 51.0
sensors/greenhouse/fan = off
sensors/greenhouse/temperature = 26.18
sensors/greenhouse/humidity = 50.7
```

## Not tested

- Exhausting the hourly flash budget (30 uploads)
- Unplug and replug (needs a person at the board)
- I2C identification against real devices (nothing is wired to the Uno)
- ESP32 backup/restore/crash decode, Pico UF2, Raspberry Pi GPIO

## Reproduce

See [mcp/hardware_validation/README.md](mcp/hardware_validation/README.md). Earlier runs and the full log are in [docs/hardware-validation.md](docs/hardware-validation.md).
