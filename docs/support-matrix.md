# Hardware support matrix

This is the contract for what each device family can do. Empty cells in
[`hardware-validation.md`](hardware-validation.md) mean **experimental**, not
supported. A mock, PTY, or compile test is not physical validation.

Availability:

| Value | Meaning |
|---|---|
| supported | Implemented and physically validated |
| experimental | Implemented in software; no physical validation row yet |
| unsupported | Not implemented. Tools must return `UNSUPPORTED_OPERATION`. |

Safety is separate: `read_only`, `state_changing`, `destructive`.

## Raspberry Pi

Pi 3 / 4 / 5 over SSH. GPIO uses fixed `pinctrl` / `raspi-gpio` argv only.

| Operation | Availability | Safety | Confirm | MCP tool |
|---|---|---|---|---|
| `pi.inventory` | experimental | read_only | no | `pi_inventory` |
| `pi.status` | experimental | read_only | no | `pi_status` |
| `gpio.list` | experimental | read_only | no | `gpio_list_pins` |
| `gpio.read` | experimental | read_only | no | `gpio_read_pin` |
| `gpio.set_mode` | experimental | state_changing | yes | `gpio_set_mode` |
| `gpio.write` | experimental | destructive | yes | `gpio_write_pin` |
| `gpio.pwm` | unsupported | state_changing | yes | — |
| `gpio.spi` | unsupported | state_changing | yes | — |
| `gpio.i2c` | unsupported | state_changing | yes | — |

Jetson hosts are not Raspberry Pi hosts. Do not run `pinctrl` on Jetson.

## Jetson (Orin first)

Separate Linux family. No GPIO tools. No MCP inventory tools yet.

| Operation | Availability | Safety | Confirm | MCP tool |
|---|---|---|---|---|
| `jetson.inventory` | unsupported | read_only | no | — |
| `jetson.status` | unsupported | read_only | no | — |
| `jetson.telemetry` | unsupported | read_only | no | — |
| `gpio.write` | unsupported | destructive | yes | — |

Other Jetson generations stay experimental until they have a validation row.

## Microcontroller (Arduino, ESP32, RP2040)

USB serial on the Omarchy machine. Flash goes through `arduino-cli` and
refuses unidentified boards.

| Operation | Availability | Safety | Confirm | MCP tool |
|---|---|---|---|---|
| `board.list` | experimental | read_only | no | `list_boards`, `describe_board` |
| `serial.open` | experimental | state_changing | no | `serial_open` |
| `serial.read` | experimental | read_only | no | `serial_read` |
| `serial.write` | experimental | destructive | no | `serial_write` |
| `flash.compile` | experimental | read_only | no | `compile_sketch` |
| `flash.upload` | experimental | destructive | yes | `upload_sketch` |

CH340/CP210x clones without an exact VID/PID match stay unidentified and
cannot be flashed.

## Siemens LOGO! and S7-1200

Industrial protocols are not enabled.

| Operation | Availability | Safety | Confirm | MCP tool |
|---|---|---|---|---|
| `plc.discover` | unsupported | read_only | no | — |
| `plc.read` | unsupported | read_only | no | — |
| `plc.write` | unsupported | destructive | yes | — |

## Querying the matrix

`list_capabilities` returns this table as structured JSON. Call it with an
optional `family` of `raspberry_pi`, `jetson`, `microcontroller`,
`siemens_logo`, or `siemens_s7`.
