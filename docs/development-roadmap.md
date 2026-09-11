# Development roadmap

This project aims to become an Omarchy-centered hardware development
workstation, not a general-purpose remote shell. The roadmap covers Raspberry
Pi 3/4/5, Raspberry Pi OS Bookworm and Omarchy, Arduino/ESP32/RP2040 boards,
Jetson Orin and related Jetson systems, and selected Siemens LOGO! and S7-1200
workflows.

The current plugin already provides USB board discovery, serial sessions,
Arduino compilation and guarded flashing, and fixed-command Raspberry Pi GPIO
over SSH. The remaining hardware-specific capabilities are not supported until
they are implemented and physically validated.

## Principles

- **Read-only first.** Inventory, health, diagnostics, and protocol discovery
  precede any state-changing operation.
- **No remote shell.** Remote operations use fixed commands or typed protocol
  adapters. Never add arbitrary command execution or unrestricted file transfer.
- **Explicit authorization.** GPIO, firmware, and PLC writes require narrow
  allowlists, bounds, confirmation, timeouts, rate limits, and audit records.
- **Honest support claims.** A mock, PTY, simulator, or compile test does not
  count as physical validation. Results belong in
  [`hardware-validation.md`](hardware-validation.md).
- **Omarchy-native UX.** The bar and panel should expose capabilities, health,
  errors, and setup state without blocking the shell process.

## Stages

### 1. Contract and capability model

Define the support matrix and shared typed device/capability records. Include
stable identity fields, capability negotiation, health states, unsupported
operation errors, and redacted diagnostics. Document the distinction between
Raspberry Pi GPIO, Jetson hardware, USB microcontrollers, and industrial PLC
protocols.

### 2. Raspberry Pi development target

Prioritize Raspberry Pi OS Bookworm on Pi 3/4/5, with Omarchy as the local
control-plane environment. Add read-only inventory for model, OS, kernel,
temperature, throttling/undervoltage, storage, network, GPIO backend, and
available tools. Test backend differences explicitly; do not silently assume
that Pi 5 and earlier models expose identical GPIO behavior.

Potential later capabilities include GPIO edge observation, PWM, SPI, and I2C.
Each requires a separate electrical-safety design, resource ownership policy,
bounded output, and physical tests. The existing SSH host-key, host allowlist,
fixed-argv, timeout, and output-boundary controls remain mandatory.

Reference:

- [Raspberry Pi OS documentation](https://www.raspberrypi.com/documentation/computers/os.html)
- [Raspberry Pi configuration documentation](https://www.raspberrypi.com/documentation/computers/configuration.html)
- [Raspberry Pi remote access documentation](https://www.raspberrypi.com/documentation/computers/remote-access.html)

### 3. Jetson edge-compute target

Treat Jetson as a separate Linux hardware family rather than a Raspberry Pi
GPIO variant. Start with Jetson Orin inventory and read-only telemetry:
model, JetPack/L4T and kernel versions, power mode, temperatures, CPU/GPU/
memory utilization, storage, USB, and serial devices. Add other Jetson
generations only after their capabilities and permissions are tested.

Choose a transport deliberately: fixed SSH operations may be sufficient for
diagnostics, while richer functions may need a separately installed,
authenticated, least-privilege helper. No helper may become a generic command
runner.

### 4. Microcontroller workflows

Expand board profiles for Arduino, ESP32, and RP2040 families. Improve
multi-board identification, reset/reconnect handling, project metadata,
compile status/cache reporting, and panel actions. Keep serial output
untrusted and preserve the existing flash token, FQBN, board preflight,
confirmation, and audit requirements.

### 5. Industrial protocol adapters

Research Siemens LOGO! and S7-1200 connectivity, supported firmware, transport,
addressing, libraries, and licensing before selecting an implementation.
Begin with read-only device discovery and diagnostics. Represent PLC values as
typed tags or data blocks, not free-form commands.

Any future write support must use configured address/type allowlists, value
bounds, explicit confirmation, durable audit logs, clear connection identity,
and offline protocol fixtures. Simulation and real PLC validation must be
reported separately. Industrial-control support is never enabled by default.

### 6. Lab workflows and Omarchy UX

Add topology and capability views for configured remote hosts and locally
connected devices. Provide setup diagnostics, redacted exportable reports, and
repeatable validation commands. Consider provisioning only as previewable,
fixed recipes with backup/rollback and no hidden privileged changes.

Extend the bar panel with device grouping, health states, capability-aware
actions, last-seen/error details, and clear setup guidance. All QML work must
remain asynchronous.

### 7. Validation and release

Every adapter starts with unit tests, protocol fixtures, mock SSH, and PTY
coverage that runs without hardware. Then record physical tests for Pi 3/4/5,
at least one supported USB board, Jetson Orin when available, and Siemens
hardware only when actually connected and tested. Run the existing Python,
Ruff, ShellCheck, manifest/version, dependency, workflow, and Omarchy checks.
Update the changelog, threat model, security policy, and version fields
together. Release and marketplace updates follow evidence, not intent.

## Explicit non-goals

This roadmap does not authorize a general remote terminal, unrestricted remote
file operations, credential collection, stealth persistence, automatic firmware
or PLC writes, or claims of universal compatibility across hardware families.
