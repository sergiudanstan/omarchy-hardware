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

Availability is per family. **Supported on** lists the exact boards (FQBNs) an
operation passed physical validation on; `list_capabilities` returns them as
`supported_boards`. Every other board in the family keeps the row's availability.

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

Separate Linux family. Hosts go in `[jetson] hosts`, never `[pi] hosts`. No GPIO.

| Operation | Availability | Safety | Confirm | MCP tool |
|---|---|---|---|---|
| `jetson.inventory` | experimental | read_only | no | `jetson_inventory` |
| `jetson.status` | experimental | read_only | no | `jetson_status` |
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

| Operation | Availability | Supported on | Safety | Confirm | MCP tool |
|---|---|---|---|---|---|
| `board.list` | experimental | `arduino:avr:uno` | read_only | no | `list_boards`, `describe_board` |
| `board.fingerprint` | experimental | — | state_changing | no | `fingerprint_board` |
| `serial.open` | experimental | `arduino:avr:uno` | state_changing | no | `serial_open` |
| `serial.read` | experimental | `arduino:avr:uno` | read_only | no | `serial_read`, `serial_expect` |
| `serial.write` | experimental | `arduino:avr:uno` | destructive | yes | `serial_write`, `serial_query` |
| `flash.compile` | experimental | `arduino:avr:uno` | read_only | no | `compile_sketch` |
| `flash.upload` | experimental | `arduino:avr:uno` | destructive | yes | `upload_sketch` |

Arduino Uno: validated 2026-09-16, see
[`hardware-validation.md`](hardware-validation.md). Unplug/reconnect and
disconnected-port uploads were not part of that run.

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

`weintek_mqtt_publish` is live: it publishes one UTF-8 value (at most 4096 bytes,
QoS 0 or 1) to an exact host, port and topic from `[[weintek.mqtt]]`, over TLS
unless that target carries `allow_insecure`. Each publish needs `confirm=true`,
counts against `[pi] actuation_budget_per_min` for that topic, and is written to
the audit log before and after it is sent. It is experimental: tested against an
in-process broker, not yet against a physical cMT panel.

`weintek_opcua_read` and `weintek_opcua_write` talk to the HMI's built-in OPC UA
server (EasyBuilder Pro: [IIoT] > OPC UA Server) through `asyncua`. The endpoint
and node id must match a `[[weintek.opcua]]` entry exactly. Sessions are signed and
encrypted with a client certificate **and a pinned HMI certificate** (`trust_list`)
unless the entry sets `allow_insecure` with mode `None`. Writes need `confirm=true`,
accept only scalar Boolean, integer (range-checked), Float, Double and String nodes,
convert the text value to the node's own type, return the previous and new values,
and are audited and rate-limited per node. Tested against an in-process asyncua
server, including a Basic256Sha256 SignAndEncrypt session; not yet against a panel.

`weintek_hmi_identify` opens the same secured session to a listed endpoint and
reads only the standard OPC UA Server object: product name and URI, manufacturer,
software version, build number and date, server state, start and current time, and
the namespace array (useful for finding the `ns=` index of HMI tags). No
application node needs to be allowlisted for it. `reports_weintek` is what the
server says about itself, not proof of the hardware.

`weintek_mqtt_subscribe` listens on one exact allowlisted topic (1-30 s, up to
100 messages) and returns what arrives, the retained value first. Messages on
any other topic are dropped even if the broker sends them.

`weintek_modbus_read` reads HMI memory from a panel whose EasyBuilder Pro project
runs the **MODBUS Server** driver on Ethernet: `LW-n` is holding register n
(FC03), `RW-n` holding register 9999+n (4x 10000 onwards), `LB-n` coil n (FC01),
as in the EasyBuilder Pro manual, chapter 19. Up to 64 words or 256 bits per
call, and the whole range must sit inside one `read` entry of a
`[[weintek.modbus]]` target. Modbus TCP has no authentication and no encryption,
so every target needs `allow_insecure = true` and writes are not offered.

| Operation | Availability | Safety | Confirm | MCP tool |
|---|---|---|---|---|
| `hmi.identify` | experimental | read_only | no | `weintek_hmi_identify` |
| `opcua.read` | experimental | read_only | no | `weintek_opcua_read` |
| `opcua.write` | experimental | destructive | yes | `weintek_opcua_write` |
| `mqtt.subscribe` | experimental | read_only | no | `weintek_mqtt_subscribe` |
| `modbus.read` | experimental | read_only | no | `weintek_modbus_read` |
| `mqtt.publish` | experimental | destructive | yes | `weintek_mqtt_publish` |

Family: `weintek_hmi`.

## MING stack (MQTT, InfluxDB, Node-RED, Grafana)

The common IoT stack a Pi or lab machine publishes into, reached on this
machine or across the network. Each target is a named `[[ming.*]]` entry in
`config.toml`; `[ming] allow` defaults to `false`.

- **MQTT**: `subscribe` holds topic filters. A tool call may narrow one
  (`plant/#` → `plant/line1/+`) but never widen it, and received messages are
  re-checked against the filter. `publish` holds exact topics, no wildcards, no
  `$` topics. QoS 0 and 1 only.
- **InfluxDB 2.x**: queries are built from typed parameters (bucket,
  measurement, field, tag equality, time range, one aggregate). Raw Flux is not
  accepted, since Flux can write (`to()`) and make HTTP and SQL calls. Writes are
  one point of line protocol built from typed fields.
- **Node-RED**: `/flows` is read and summarised (function-node code is left
  out) and allowlisted inject nodes can be triggered. Deploying flows is
  unsupported by design: function and exec nodes make it a remote shell.
- **Grafana**: dashboard search, and annotations when `annotate = true`.

Cleartext is accepted to loopback only; anything else needs TLS/HTTPS or an
explicit `allow_insecure`. HTTP redirects are not followed and proxy variables
are ignored, so a token only goes to the configured host. Credentials are named
by environment variable or mode-600 file, never stored in the config.

| Operation | Availability | Safety | Confirm | MCP tool |
|---|---|---|---|---|
| `ming.status` | experimental | read_only | no | `ming_status` |
| `ming.mqtt.subscribe` | experimental | read_only | no | `mqtt_subscribe` |
| `ming.mqtt.publish` | experimental | destructive | yes | `mqtt_publish` |
| `ming.influx.measurements` | experimental | read_only | no | `influx_measurements` |
| `ming.influx.query` | experimental | read_only | no | `influx_query` |
| `ming.influx.write` | experimental | state_changing | yes | `influx_write` |
| `ming.nodered.flows` | experimental | read_only | no | `nodered_flows` |
| `ming.nodered.inject` | experimental | destructive | yes | `nodered_inject` |
| `ming.nodered.deploy` | unsupported | destructive | yes | — |
| `ming.grafana.dashboards` | experimental | read_only | no | `grafana_dashboards` |
| `ming.grafana.annotate` | experimental | state_changing | yes | `grafana_annotate` |

Experimental: tested against in-process fake servers, and run end to end
against [`examples/ming-stack`](../examples/ming-stack) on x86_64 (2026-09-18),
including the refusal paths. Not yet run against a stack on a Pi. A publish or
inject is destructive because whatever subscribes to it may move real equipment.

Family: `ming_stack`.

## Querying the matrix

`list_capabilities` returns this table as structured JSON. Call it with an
optional `family` of `raspberry_pi`, `jetson`, `microcontroller`,
`siemens_logo`, `siemens_s7`, `omron`, `schneider`, `weintek_hmi`, or `ming_stack`.

`hardware_report` is a separate local, read-only diagnostic. It combines
connected-board state, STM32 debug probes and DFU bootloaders (redacted serials),
and open-session state with this matrix and reports only the
count of configured remote hosts; it never exports hostnames, credentials, or
other private configuration values.
