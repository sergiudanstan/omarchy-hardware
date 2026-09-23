# Formal verification (Lean 4)

Machine-checked proofs about the parts of the plugin that decide what may reach a device
or a broker. Each Lean definition transcribes one Python function; a differential test
runs both on the same random inputs to show they compute the same thing.

```sh
bin/verify-lean.sh            # build + proofs, axiom audit, 31k differential cases
bin/verify-lean.sh --cases 20000 --seed 7
```

You need Lean 4.34.0 (`formal/lean-toolchain`; `elan` picks it up) and the package
dependencies from `mcp/requirements.lock`. CI runs the same script in the `lean proofs` job.

## What is proven

| Theorem | Python | Statement |
|---|---|---|
| `Mqtt.filterCovers_sound` | `filter_covers`, `topic_matches` | If `check_ming_subscribe` accepts a narrowed filter, every topic it matches is matched by the allowlisted filter too, including the `$`-topic rule. Narrowing never widens. |
| `Mqtt.filterCovers_refl` | `filter_covers` | Every allowlisted filter covers itself. |
| `Mqtt.sys_topics_hidden` | `topic_matches` | A filter that starts with `+` or `#` never matches a `$` topic (spec 4.7.2). |
| `Mqtt.decode_encode` | `_encode_length`, `Client._read_packet` | Every length up to 268,435,455 decodes back to itself, and the decoder consumes exactly the encoded bytes. |
| `Mqtt.encodeLength_bytes`, `encodeLength_fits` | `_encode_length` | Each byte is below 256, and the encoding is at most 4 bytes. |
| `Mqtt.nextPacketId_range`, `nextPacketId_cycle` | `Client._packet_id` | The ids stay in 1..65535, never 0, and wrap from 65535 to 1. |
| `Modbus.authorised_cells` | `check_weintek_modbus`, `modbus_address` | Every (function, address) cell a read touches is a cell some allowlist entry names. |
| `Modbus.wire_fits_u16` | `weintek_modbus_read` → `struct.pack` | With a valid allowlist and the tool's count bound, the address, the count and the last register all fit 16 bits. |
| `Modbus.lw_rw_disjoint`, `lb_separate_table` | `modbus_address` | LW and RW map to disjoint holding-register addresses, and LB reads use a separate function. |
| `Modbus.counts_within_spec` | `MAX_MODBUS_*` | The tool's count limits stay under the protocol limits (2000 coils, 125 registers), and the reply byte count fits one byte. |
| `Modbus.covered_in_area` | `ModbusRange.covers` | A covered read of a valid range stays inside its HMI memory area. |
| `Budget.budget_safe` | `RollingBudget.charge` | For any charges with non-decreasing timestamps, **every** window of `window` seconds (not only the windows ending at a charge) admits at most `limit` units. This covers the write, actuation, flash and MING budgets. |

`Axioms.lean` shows the axioms each theorem uses. The script fails on anything beyond
`propext`, `Classical.choice` and `Quot.sound`, or on `sorry`, `admit` or `native_decide`
anywhere in the sources.

## Why you can trust the link to Python

The proofs cover the Lean definitions, not the Python code. `tests/differential.py`
connects the two. It imports the real functions from `mcp/omarchy_hardware`, including
`Client._read_packet` with only the socket read stubbed and `RollingBudget` with a
controlled clock. It then compares their answers with `oracle`, a program compiled from the
same Lean definitions. The default run has 31,213 cases, and all of them must agree.

## Limits

* The differential test samples inputs at random. It does not cover every input, so the
  link between Lean and Python is tested, not proven.
* Budget timestamps are integers in the model. Python uses a float clock. Counts are
  natural numbers, which holds because every caller passes `len(...)` or 1.
* The Modbus theorems assume the allowlist passed `config._modbus_range`. `policy`
  does not check that again for a `Config` built in code.
* Out of scope: path and symlink checks (`resolve_port`), SSH, network I/O, OPC UA, and
  anything about real hardware.

## Findings

* **Whole-area Modbus ranges are rejected.** `MODBUS_RANGE` allows at most four count
  digits, so `RW-0:55536` and `LB-0:12800` fail with "must look like 'LW-100'". This fails
  closed and is safe. To cover a whole area, list more than one entry. The differential
  test found this before the model included the digit limits.
* The remaining-length decoder accepts non-minimal encodings, for example `0x80 0x00`
  for 0. It stays consistent with the proof and is harmless.
