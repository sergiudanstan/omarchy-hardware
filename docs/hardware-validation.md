# Hardware validation log

Physical checks for flashing and Raspberry Pi GPIO. Unit tests and CI do not
stand in for this file: they never open a real `/dev/ttyACM*` or SSH to a Pi.

Fill a row only after running the command on the named hardware. Empty cells mean
the check has not been done. Do not invent results.

## MING stack — 2026-09-23

A full check of `examples/ming-stack`, with the simulated greenhouse, by
[`run_ming.py`](../mcp/hardware_validation/run_ming.py): **24/24**
([result](hardware-validation/ming-2026-09-23.json)), after three stack fixes that the
first run exposed ([first run, 20/24](hardware-validation/ming-2026-09-23-before-fixes.json)).
Details are in [TEST-RESULTS.md](../TEST-RESULTS.md#ming-stack--full-check-2026-09-23).
The greenhouse values are simulated, not sensor readings.

## Latest run — 2026-09-22

A readable summary of this run, with every check listed, is in [TEST-RESULTS.md](../TEST-RESULTS.md).

Against the released **v0.1.5** (installed plugin at `e04f705`), driven through the
installed MCP stdio launcher by Claude at Sergiu-Dan Stan's request.

- **Baseline, 26/26** ([result](hardware-validation/uno-2026-09-22.json)): the same
  `run_uno.py` checks as 2026-09-21, all passing on v0.1.5.
- **Extended, 31/31** ([result](hardware-validation/uno-extended-2026-09-22.json)):
  `run_uno_extended.py` covers every Uno-applicable tool the baseline does not:
  - profiles, reference and wiring checks (an LED on D13 passes; a motor on D9 without a driver fails)
  - labels read back through `describe_board` and `board_history`
  - the read-only I2C bench probe: flashed, report caught with `serial_expect`, fed to
    `identify_i2c`, banner recognised by `fingerprint_board`
  - `serial_expect` timeouts; `serial_clear`, `list_sessions` and `serial_status`
  - refusals: `firmware_backup` gives `UNSUPPORTED_OPERATION`; `decode_crash` on non-crash text;
    `mpy_list` on an Arduino; an upload to a missing port gives `PORT_NOT_FOUND`
  - the journal records both uploads, and the audit log pairs every `upload_started`
    with `upload_finished`
  - with the local MING stack, a **serial→MQTT bridge** from the probe to
    `actuators/uno-probe`: six JSON objects arrived through `mqtt_subscribe`, the
    bridged session refused `serial_read`, and the start and stop were audited
- **First extended run, 30/31** ([result](hardware-validation/uno-extended-2026-09-22-first-run.json)):
  the one failure was the runner's own assumption that `list_boards` carries labels.
  In v0.1.5 it did not (it does since); `describe_board` and `board_history` do. The check was corrected
  and the whole suite re-run.
- **NIS2 evidence: 38 pass, 0 fail, 2 not applicable, 4 limitations**
  ([result](hardware-validation/nis2-2026-09-22.json)), mapped in [nis2.md](nis2.md).

What crossed MQTT was the probe's own diagnostic JSON (`{"probe":"done","bus":"Wire","i2c":[]}`
and `{"selftest":true,"i2c_count":0}`). These are **not sensor readings**. Nothing is
wired to the Uno, so the I2C bus is empty. The board is left on the validation sketch,
answering `PING` with `PONG`. `config.toml` was restored byte-identical, and the MING
stack was stopped again.

Not validated: I2C identification of real devices, exhausting the hourly flash budget,
unplug/replug, ESP32, Pico, Raspberry Pi and Jetson.

## Previous run — 2026-09-21

**26/26 physical checks passed** through the installed plugin's MCP stdio
launcher, using code at `e082968819751d1754962cc625864487ae5f9875`.
The development and installed tracked files matched before the run. The
[redacted result](hardware-validation/uno-2026-09-21.json) records each tool result,
software versions, and hashes of the validation sketch and runner.

The Uno was flashed twice: a normal upload (~3.8 s), then an upload with an open
serial session (~3.7 s). The restored session read `HWVAL READY`; `PING` → `PONG`,
echo, read timeout, and serial close all passed. Missing confirmation, a wrong
FQBN, forged token, changed digest, a non-allowlisted port and an outside-root
sketch were refused with the expected error codes. The board is left running the
serial-only validation sketch at 115200 baud, with all test sessions closed.

This run validates the Arduino Uno path, including regressions after the recent
Pico changes. It does **not** validate Pico UF2 writeback, wireless variant
selection on a physical Pico, Pi GPIO, Jetson operations, unplug/reconnect or board swapping.
The [2026-09-16 baseline](hardware-validation/uno-2026-09-16.json) is retained.

## MING data path — 2026-09-21

With the existing echo sketch on the Uno, a separate host adapter sent three
USB-returned diagnostic numbers through MQTT/TLS, the greenhouse Node-RED flow,
InfluxDB and Grafana's datasource. The paced run passed **12/12 checks**. The
initial rapid run retained only two of three InfluxDB points; the flow uses
second-resolution timestamps. Both runs are preserved in the
[Arduino MING tutorial](../examples/ming-stack/arduino#7-interpret-failures-and-the-recorded-runs).

These are diagnostic echoes, not actual sensor readings. No continuous bridge,
GPIO actuator, browser rendering or Pi-hosted stack was validated. The MING
check does not flash firmware or deploy flows.

## Environment

| Field | Value |
|---|---|
| Date | 2026-09-22 |
| Operator | Claude, at Sergiu-Dan Stan's request (2026-09-21 run: Codex) |
| Host OS / Omarchy version | Omarchy 4.0.4-1, kernel 7.2.5-3-omarchy |
| Plugin version (`manifest.json`) | 0.1.5 (commit `e04f705`) |
| `arduino-cli version` | 1.4.1 (`arduino:avr` 1.8.8) |
| Pi model / OS | |
| Board under test | Arduino Uno, USB `2341:0043`, `/dev/ttyACM0` |

## USB boards

| Check | Board | Command or tool | Result | Notes |
|---|---|---|---|---|
| Discovery (`list_boards` / bar widget) | Arduino Uno | `list_boards`, `describe_board` via MCP stdio | Pass | Identified as `arduino:avr:uno`; `/dev/ttyS4` not listed. [run](hardware-validation/uno-2026-09-21.json) |
| Serial open / read / write / close | Arduino Uno | `serial_open`, `serial_read`, `serial_write`, `serial_query`, `serial_close` | Pass | Banner read after reset; `PING` → `PONG`; echo round-trip; silent read times out; no session left open. [run](hardware-validation/uno-2026-09-21.json) |
| Reconnect after unplug | | | | |
| `compile_sketch` produces `.hex` | Arduino Uno | `compile_sketch` | Pass | `.hex` in the artifact dir; a sketch outside `sketch_roots` is refused with `SKETCH_NOT_ALLOWED`. [run](hardware-validation/uno-2026-09-21.json) |
| `upload_sketch` without `confirm` refused | Arduino Uno | `upload_sketch confirm=false` | Pass | `FLASH_UNCONFIRMED`. Wrong FQBN → `BOARD_MISMATCH`; forged token or different digest → `INVALID_TOKEN`. [run](hardware-validation/uno-2026-09-21.json) |
| `upload_sketch` with token + `confirm` | Arduino Uno | `upload_sketch confirm=true` | Pass | Flashed in ~3.8 s; the board then ran the new sketch. With a session open, it was closed and restored (`session_restored: true`). [run](hardware-validation/uno-2026-09-21.json) |
| Upload after swapping a different board | | | | |
| Upload with the port disconnected | | | | |

## Raspberry Pi GPIO

Use a pin that is not driving a load you cannot afford to toggle. BCM 0 and 1
must remain refused.

| Check | Host | Pin | Command or tool | Result | Notes |
|---|---|---|---|---|---|
| `pi_status` | | — | | | |
| `pi_inventory` | | — | | | |
| Throttling / `vcgencmd` | | — | | | |
| Storage `df -P /` | | — | | | |
| Network operstate | | — | | | |
| Unknown host key refused | | — | | | |
| Host not in `config.toml` refused | | — | | | |
| `gpio_list_pins` | | — | | | |
| `gpio_read_pin` | | | | | |
| `gpio_set_mode` | | | | | |
| `gpio_write_pin` | | | | | |
| BCM 0 / 1 refused | | 0 or 1 | | | |
| Malformed remote output | | | | | |

## Jetson

Hosts must be in `[jetson] hosts`. GPIO tools must refuse them.

| Check | Host | Command or tool | Result | Notes |
|---|---|---|---|---|
| `jetson_status` | | | | |
| `jetson_inventory` | | | | |
| L4T / `nv_tegra_release` | | | | |
| `nvpmodel -q` | | | | |
| Host not in `[jetson] hosts` refused | | | | |
| GPIO tools refuse a Jetson host | | | | |

## Limitations observed

Record anything the software cannot see (missing USB serial, missing `pinctrl`,
session restore after upload, and so on).

- 2026-09-21, Arduino Uno: `list_boards` suggests 9600 baud for the Uno; the
  validation sketch runs at 115200 and was opened at that rate explicitly.
- 2026-09-22: a unit test wrote 31 fake `firmware_restore_failed` records into the
  real audit log between 2026-09-19 and 2026-09-22 (backup id
  `20260919-120000-aaaaaaaaaaaa`). The tests now isolate the audit log, the journal and
  the Slack workspace. The records stay, because removing them would break the
  hash chain; NIS2-B-5 names them.
- 2026-09-22: the example Mosquitto ACL grants the `claude` user read on `sensors/#`
  and readwrite on `actuators/#` only, so the bridge test used `actuators/uno-probe`.
  Nothing subscribes to it: the greenhouse device was stopped, and Node-RED reads
  only `sensors/#`. Mosquitto acknowledges a subscription the ACL forbids and then
  withholds its messages, so a refused subscribe is not observable as an error.
- Upload tokens are bound to sketch, FQBN, USB serial and artifact digest, and
  expire after their TTL. They are not single-use: the same token can flash the
  same artifact again until it expires. Not exercised on hardware.
- Unplug, board-swap and disconnected-port checks need someone at the machine and
  were not run.

The USB run is repeatable with
[`mcp/hardware_validation/run_uno.py`](../mcp/hardware_validation/README.md).
