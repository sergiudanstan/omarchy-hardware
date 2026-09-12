# Model Hardware Standard preparation

Status checked: **2026-09-12**. This project prepares metadata for a future MHS
adapter; it does not implement or claim conformance to an official MHS version.

Anthropic announced a research preview on August 27, 2026, describing device
drivers, discovery, device reference information, and access through MCP, CLI,
and APIs. The announcement says open sourcing will follow work with preview
partners. We have not obtained the official specification, SDK, or conformance
fixtures, so no wire format or release date is assumed here.
Sources: [Anthropic announcement](https://www.anthropic.com/news/model-hardware-standard-research-preview)
and [official preview site](https://www.modelhardwarestandard.com/).

## Research preview application

The official preview site links to the [MHS access application](https://forms.gle/UdQ8JubjMN1R5CJt8).
Application status: **draft prepared; not submitted**. The form could not be
read by the research browser, so the text below is a reusable project summary,
not a verified field-by-field response.

### Project and intended use

> I maintain Omarchy Hardware, an open-source hardware integration project for
> Linux. It provides USB development-board discovery, serial communication,
> guarded Arduino compilation/upload, and Raspberry Pi and Jetson diagnostics
> through an MCP server.
>
> I would like to join the MHS research preview to develop an adapter for
> accessible development hardware and contribute integration feedback and
> reproducible tests.
>
> Our preparation includes machine-readable capability descriptions,
> operation-to-tool mappings, policy metadata, and MCP interoperability tests.
> Existing controls include target allowlists, write limits, explicit
> confirmation parameters, and compile-bound upload tokens. Physical hardware
> validation remains incomplete, and we do not yet claim MHS compatibility.
>
> Repository: https://github.com/sergiudanstan/omarchy-hardware
>
> Preparation PR: https://github.com/sergiudanstan/omarchy-hardware/pull/18
>
> We are seeking access to the official specification, reference drivers, SDK,
> and validation guidance.

Before submitting, the maintainer should supply the requested contact details,
affiliation if applicable, and the hardware actually available for testing.
The repository's supported-family list is not an inventory of owned devices.
Keep personal application details out of the public repository. Record the
submission date and access outcome here when known.

## Available now

`get_hardware_reference(family=None)` is a read-only MCP tool. Its JSON contains
the existing family support matrix, MCP entry-point names, and selected current
configuration values. `format: omarchy-hardware-reference` and `format_version: 1`
version our own export, not the MHS protocol. The `mhs` block explicitly reports
`preparation_only`, `compatible: false`, and no specification version.

The exporter is also a Python API (`export_reference(config, family=None)`) and
a local CLI. From the repository root after installing the plugin dependencies:

```bash
PYTHONPATH="$PWD/mcp" \
  ~/.local/share/omarchy-hardware/venv/bin/python -B \
  -m omarchy_hardware.reference --family microcontroller
```

Omit `--family` for all families. This reads local configuration but does not
scan devices, open serial sessions, compile code, or contact SSH hosts.
Hostnames, USB serials, sketch paths, and OPC UA/MQTT targets are not exported.
Availability describes implementation status, not connected-device readiness.
Unsupported rows may name an existing stub tool; they remain unsupported.

## Claude Code connection

After this change is installed with the plugin, register its existing local
stdio server from the project where you want Claude to use it:

```bash
claude mcp add --transport stdio omarchy-hardware -- \
  "$HOME/.config/omarchy/plugins/io.github.sergiudanstan.hardware/bin/hardware-mcp"
```

This is a manual configuration step; setup does not register it automatically.
The command registers the full existing hardware MCP server, including its
guarded state-changing tools. It is not a separate read-only server.
Use `/mcp` inside Claude Code to check the connection, then ask:

> Call get_hardware_reference and summarize the available families, policy
> limits, unsupported operations, and MHS readiness. Do not operate hardware.

For an isolated development checkout, select its code explicitly without
reinstalling the active plugin:

```bash
claude mcp add --transport stdio omarchy-hardware-dev -- \
  /usr/bin/env "PYTHONPATH=$PWD/mcp" \
  "$HOME/.local/share/omarchy-hardware/venv/bin/python" -B \
  -m omarchy_hardware.server
```

Run that command from the development repository root. It still reads the
normal hardware configuration. Registration syntax follows
[Claude Code's MCP documentation](https://code.claude.com/docs/en/mcp).
Cloud-hosted clients that cannot launch local stdio processes need a separately
designed authenticated transport; this change adds no network listener.

## Adapter boundary when the official specification is available

1. Record the official specification revision, license, SDK source, and pinned
   dependency hashes. Replace the preparation status only after conformance
   tests pass against that revision.
2. Translate the project reference into the actual driver and discovery schema.
   Keep schema conversion separate from execution. Unknown operations and
   unimplemented families must fail explicitly.
3. Route operations through the existing guarded MCP tools. Calling low-level
   `flash` or serial methods directly skips protections enforced in `server.py`.
   Preserve host/pin allowlists, serial limits, confirmation, sketch roots,
   compile tokens, board revalidation, and upload audit requirements.
4. Add per-device identity, state, units, operating bounds, and evidence from
   the actual device and wiring. A USB ID does not establish safe voltage,
   current, connected load, motion range, or emergency-stop behavior. These
   physical properties are currently unspecified in our reference.
5. Design resource ownership, cancellation, timeout recovery, and interlocks
   before enabling multi-device workflows. Never automatically retry a write
   when its outcome is unknown. A metadata snapshot is not authorization;
   execution must reload and enforce current policy.
6. Run official conformance tests and device-specific fault tests. Record real
   results in [hardware-validation.md](hardware-validation.md) before claiming
   device support. MCP integration tests alone do not establish MHS compatibility.

## Existing blockers for firmware integration

The review of `ed56b35` identified these unresolved board-mapping problems:

- ST-LINK `0483:374b` and `0483:374e` identify shared debug interfaces, not a
  specific Nucleo target. Preserve ambiguity until the MCU/board is verified.
- micro:bit `0d28:0204` is shared; the entry labelled v2 currently selects the
  v1 `BBCmicrobit` FQBN. Disambiguate hardware before selecting the target.
- Pro Micro 5V and 3.3V mappings need their respective CPU/clock options. The
  upload check currently rejects an explicit option that differs from the
  generic suggested FQBN.

These are prerequisites for future MHS firmware execution, not fixes provided
by the metadata exporter. The existing `confirm=true` parameter is controlled
by the calling model and does not prove a human approved an operation.

Board definition evidence: [STM32 core](https://github.com/stm32duino/Arduino_Core_STM32/blob/main/boards.txt),
[nRF5 core](https://github.com/sandeepmistry/arduino-nRF5/blob/master/boards.txt),
and [SparkFun AVR core](https://github.com/sparkfun/Arduino_Boards/blob/main/sparkfun/avr/boards.txt).

## Validation

`mcp/tests/test_reference.py` verifies redaction, policy refresh, support status,
tool bindings, CLI output, and a real local MCP initialization/discovery/reference
exchange. It uses temporary configuration and never calls hardware operations.
