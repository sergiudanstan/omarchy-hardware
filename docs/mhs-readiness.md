# Model Hardware Standard preparation

Status checked: **2026-11-14**. MHS remains an application-only research preview.
The official pages provide no public specification, SDK or conformance suite.
This project claims **no MHS compatibility or conformance**. Its reference formats
are project-owned, not an implementation of a guessed MHS protocol.

Sources: [Anthropic announcement](https://www.anthropic.com/news/model-hardware-standard-research-preview)
and [official preview site](https://www.modelhardwarestandard.com/).
Access is through the [MHS application](https://forms.gle/UdQ8JubjMN1R5CJt8).
The prior application draft has not been submitted; no preview access is assumed.
Project: [repository](https://github.com/sergiudanstan/omarchy-hardware),
[initial preparation PR](https://github.com/sergiudanstan/omarchy-hardware/pull/18).

## Mapping the preview's ideas to this plugin

| MHS idea | What exists here | Files / access |
|---|---|---|
| Driver read/write primitives | Guarded, typed operations specific to each family, with explicit unsupported rows | `server.py`, `support.py`; `list_capabilities` |
| Discoverable device descriptions | Local USB/sysfs scan plus journal labels, probed chip and user-selected board profile | `boards.py`, `journal.py`; `list_boards`; `python -m omarchy_hardware boards` |
| Per-device reference | Sourced profile facts: readable inputs, controllable outputs, electrical cautions, operation classes and policy snapshot | `reference.device_reference(id)`; `device_reference`; `hardware://devices/{profile_id}` |
| Driver/library instructions | Profile build options and peripheral setup recipes; display-controller catalog | `board_profiles.py`, `displays.py`; `board_profile`, `display_reference`; CLI `profile <id>`, `displays [controller]` |
| Safety limits | Confirmation, compile-bound tokens, live native-USB identity, upload/serial budgets, allowlists and audit | `policy.py`, `flash.py`, `server.py`; reference `limits.enforced_policy` |
| MCP, CLI and code access | Same reference facts through each interface; CLI deliberately read-only | MCP `device_reference`; CLI `reference --device <id>`; Python `reference.device_reference(id)` |

The general `get_hardware_reference` export is
`omarchy-hardware-reference`, version **2**, preserving family and policy keys
and adding a device catalog. The per-device format is
`omarchy-hardware-device-reference`, version **1**. JSON and Markdown contain
the same facts. Neither format confers permission to operate hardware.

From a checkout, without installing dependencies:

```bash
PYTHONPATH=mcp python -m omarchy_hardware profiles
PYTHONPATH=mcp python -m omarchy_hardware reference --device lilygo_t_qt_pro --markdown
bin/hardware displays ST7789
```

`reference` without `--device` exports all families and the redacted policy
snapshot. Reference generation does not scan devices, open ports, run esptool,
compile firmware or contact hosts. The `boards` command reads sysfs/journal
metadata only. Private hosts, credentials and sketch paths are not in references.

## Deliberate differences and remaining gaps

- No network discovery protocol or network listener. USB discovery is local;
  remote tools use explicitly configured targets. This is not MHS discovery.
- Primitives are per-family MCP tools, not one universal read/write verb.
  The CLI and Python reference APIs expose facts, not an alternate actuation path.
- Nothing is physically validated beyond the evidence recorded in
  [hardware-validation.md](hardware-validation.md). Sourced profiles and mocks
  are not physical validation. A profile's scoped validation note does not
  establish that every operation in its family has been tested on that board.
- Electrical limits guide wiring checks; there are no physical interlocks,
  emergency stops, connected-load models or guarantees about mechanical safety.
- Native USB identifies a chip, not a carrier. `board_probe` records evidence;
  `board_profile_assign(..., confirm=true)` binds only a profile the user chose.
  Bindings never authorize upload, backup or restore; upload still probes live.
- `confirm=true` is model-controlled and is not proof of human approval.
  The local audit chain is tamper-evident, not tamper-proof.

### Identity review of the former blocker list

`ids.py` now leaves shared ST-LINK IDs unidentified unless a known Nucleo volume
label identifies the target. Shared micro:bit `0d28:0204` is likewise unidentified,
with no guessed v1/v2 FQBN. Those unsafe guesses are no longer blockers.
`fqbn_satisfies` now accepts extra menu choices while enforcing required options.
However, Pro Micro 5 V / 3.3 V entries still suggest the same base FQBN without
voltage/clock options; variant-specific defaults remain to be reviewed.

Original board-definition sources:
[STM32 core](https://github.com/stm32duino/Arduino_Core_STM32/blob/main/boards.txt),
[nRF5 core](https://github.com/sandeepmistry/arduino-nRF5/blob/master/boards.txt),
[SparkFun AVR core](https://github.com/sparkfun/Arduino_Boards/blob/main/sparkfun/avr/boards.txt).

## Adapter boundary once a specification is public

1. Record the actual revision, license and SDK hashes; map these local formats
   to the published driver/discovery schema rather than renaming them as MHS.
2. Map operations through guarded MCP entry points. Preserve current-policy
   checks, identity rules, tokens, budgets, allowlists and audit; metadata and
   low-level Python functions must not become authorization shortcuts.
3. Add the required units, state, ownership, cancellation and recovery semantics.
   Never retry a physical write automatically when its outcome is unknown.
4. Add setup-specific bounds/interlocks and device-specific fault tests. Run the
   official conformance suite and record physical evidence before claiming support.

The stdio MCP server can be registered by the user following
[Claude Code MCP documentation](https://code.claude.com/docs/en/mcp); registration
exposes the full guarded server, not a separate read-only service. Cloud clients
that cannot launch local stdio need a separately designed authenticated transport.

## Validation

`test_profile_binding.py`, `test_device_reference.py`, `test_native_identity.py`
and `test_reference.py` cover binding refusals, live-upload independence, sourced
reference facts, CLI behavior, policy refresh, redaction, tool annotations and
MCP interoperability. No hardware operation is performed by the metadata tests.
