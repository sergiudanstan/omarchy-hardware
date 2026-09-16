# Hardware validation log

Physical checks for flashing and Raspberry Pi GPIO. Unit tests and CI do not
stand in for this file: they never open a real `/dev/ttyACM*` or SSH to a Pi.

Fill a row only after running the command on the named hardware. Empty cells mean
the check has not been done. Do not invent results.

## Environment

| Field | Value |
|---|---|
| Date | 2026-09-16 |
| Operator | Sergiu-Dan Stan |
| Host OS / Omarchy version | Omarchy 4.0.4-1, kernel 7.2.5-3-omarchy |
| Plugin version (`manifest.json`) | 0.1.2 |
| `arduino-cli version` | 1.4.1 (`arduino:avr` 1.8.8) |
| Pi model / OS | |
| Board under test | Arduino Uno, USB `2341:0043`, `/dev/ttyACM0` |

## USB boards

| Check | Board | Command or tool | Result | Notes |
|---|---|---|---|---|
| Discovery (`list_boards` / bar widget) | Arduino Uno | `list_boards`, `describe_board` via MCP stdio | Pass | Identified as `arduino:avr:uno`; `/dev/ttyS4` not listed. [run](hardware-validation/uno-2026-09-16.json) |
| Serial open / read / write / close | Arduino Uno | `serial_open`, `serial_read`, `serial_write`, `serial_query`, `serial_close` | Pass | Banner read after reset; `PING` → `PONG`; echo round-trip; silent read times out; no session left open. [run](hardware-validation/uno-2026-09-16.json) |
| Reconnect after unplug | | | | |
| `compile_sketch` produces `.hex` | Arduino Uno | `compile_sketch` | Pass | `.hex` in the artifact dir; a sketch outside `sketch_roots` is refused with `SKETCH_NOT_ALLOWED`. [run](hardware-validation/uno-2026-09-16.json) |
| `upload_sketch` without `confirm` refused | Arduino Uno | `upload_sketch confirm=false` | Pass | `FLASH_UNCONFIRMED`. Wrong FQBN → `BOARD_MISMATCH`; forged token or different digest → `INVALID_TOKEN`. [run](hardware-validation/uno-2026-09-16.json) |
| `upload_sketch` with token + `confirm` | Arduino Uno | `upload_sketch confirm=true` | Pass | Flashed in ~3.5 s; the board then ran the new sketch. With a session open, it was closed and restored (`session_restored: true`). [run](hardware-validation/uno-2026-09-16.json) |
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

- 2026-09-16, Arduino Uno: `list_boards` suggests 9600 baud for the Uno; the
  validation sketch runs at 115200 and was opened at that rate explicitly.
- Upload tokens are bound to sketch, FQBN, USB serial and artifact digest, and
  expire after their TTL. They are not single-use: the same token can flash the
  same artifact again until it expires. Not exercised on hardware.
- Unplug, board-swap and disconnected-port checks need someone at the machine and
  were not run.

The USB run is repeatable with
[`mcp/hardware_validation/run_uno.py`](../mcp/hardware_validation/README.md).
