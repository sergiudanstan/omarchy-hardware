# Hardware validation log

Physical checks for flashing and Raspberry Pi GPIO. Unit tests and CI do not
stand in for this file: they never open a real `/dev/ttyACM*` or SSH to a Pi.

Fill a row only after running the command on the named hardware. Empty cells mean
the check has not been done. Do not invent results.

## Environment

| Field | Value |
|---|---|
| Date | |
| Operator | |
| Host OS / Omarchy version | |
| Plugin version (`manifest.json`) | |
| `arduino-cli version` | |
| Pi model / OS | |
| Board under test | |

## USB boards

| Check | Board | Command or tool | Result | Notes |
|---|---|---|---|---|
| Discovery (`list_boards` / bar widget) | | | | |
| Serial open / read / write / close | | | | |
| Reconnect after unplug | | | | |
| `compile_sketch` produces `.hex` | | | | |
| `upload_sketch` without `confirm` refused | | | | |
| `upload_sketch` with token + `confirm` | | | | |
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

## Limitations observed

Record anything the software cannot see (missing USB serial, missing `pinctrl`,
session restore after upload, and so on).
