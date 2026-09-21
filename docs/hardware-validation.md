# Hardware validation log

Physical checks for flashing and Raspberry Pi GPIO. Unit tests and CI do not
stand in for this file: they never open a real `/dev/ttyACM*` or SSH to a Pi.

Fill a row only after running the command on the named hardware. Empty cells mean
the check has not been done. Do not invent results.

## Latest run — 2026-09-21

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

## Environment

| Field | Value |
|---|---|
| Date | 2026-09-21 |
| Operator | Codex, at Sergiu-Dan Stan's request |
| Host OS / Omarchy version | Omarchy 4.0.4-1, kernel 7.2.5-3-omarchy |
| Plugin version (`manifest.json`) | 0.1.4 (commit `e082968`) |
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
- Upload tokens are bound to sketch, FQBN, USB serial and artifact digest, and
  expire after their TTL. They are not single-use: the same token can flash the
  same artifact again until it expires. Not exercised on hardware.
- Unplug, board-swap and disconnected-port checks need someone at the machine and
  were not run.

The USB run is repeatable with
[`mcp/hardware_validation/run_uno.py`](../mcp/hardware_validation/README.md).
