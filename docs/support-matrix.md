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

Other Jetson generations stay experimental until they have a validation row.

## Microcontroller and hobby boards

The native identity model is intended to cover Arduino-compatible and
Arduino-like boards, including Arduino AVR/SAMD, ESP32, RP2040/Pico,
Adafruit Feather, Teensy, Seeed XIAO, STM32 Nucleo/Blue Pill, M5Stack,
micro:bit, nRF52, Waveshare RP2040/ESP32/Arduino-compatible boards, and
compatible USB CDC/serial boards. A board remains
unidentified and non-flashable when its VID/PID/profile is not recognized.

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

## Siemens, Omron, and Schneider PLCs

Industrial protocols are not enabled. Values must be typed tags, never a
remote shell or free-form command.

| Operation | Availability | Safety | Confirm | MCP tool |
|---|---|---|---|---|
| `plc.discover` | unsupported | read_only | no | — |
| `plc.read` | unsupported | read_only | no | — |
| `plc.write` | unsupported | destructive | yes | — |

Families: `siemens_logo`, `siemens_s7`, `omron`, `schneider`.

Schneider coverage is reserved for Modicon / EcoStruxure targets (M221, M340,
M580 and related). Transport (Modbus TCP, UMAS, or otherwise), addressing,
licensing, and firmware compatibility are not chosen yet. No Schneider
discovery, read, or write operation is enabled.

Omron CP/CJ/NJ/NX families are also reserved for a future typed adapter.

## Weintek HMI (OPC UA and MQTT)

Weintek cMT/MT EasyBuilder panels are a separate HMI family. The intended
transports are **OPC UA** (`opc.tcp://`) and **MQTT**. EasyAccess, project
download, and unrestricted HMI writes are out of scope.

Endpoints, OPC UA node ids, and MQTT topics must be exact allowlist entries in
`config.toml`. MQTT wildcards (`+`, `#`) are rejected. OPC UA URLs may not
include credentials. `[weintek] allow` defaults to `false`.

Live OPC UA/MQTT clients are not enabled yet. The MCP tools still enforce the
allowlist, then return `UNSUPPORTED_OPERATION`.

| Operation | Availability | Safety | Confirm | MCP tool |
|---|---|---|---|---|
| `hmi.identify` | unsupported | read_only | no | — |
| `opcua.read` | unsupported | read_only | no | `weintek_opcua_read` |
| `opcua.write` | unsupported | destructive | yes | `weintek_opcua_write` |
| `mqtt.subscribe` | unsupported | state_changing | no | — |
| `mqtt.publish` | unsupported | destructive | yes | `weintek_mqtt_publish` |

Family: `weintek_hmi`.

## Querying the matrix

`list_capabilities` returns this table as structured JSON. Call it with an
optional `family` of `raspberry_pi`, `jetson`, `microcontroller`,
`siemens_logo`, `siemens_s7`, `omron`, `schneider`, or `weintek_hmi`.

`hardware_report` is a separate local, read-only diagnostic. It combines
connected-board and open-session state with this matrix and reports only the
count of configured remote hosts; it never exports hostnames, credentials, or
other private configuration values.
