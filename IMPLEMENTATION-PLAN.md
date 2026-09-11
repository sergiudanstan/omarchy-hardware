# Action and Implementation Plan

This plan is based on the current `main` branch at commit `1dfaa10`. It
describes work to implement, not work already completed.

## Verified current state

The following statements were checked against the repository:

| Area | Current implementation |
|------|------------------------|
| SSH host-key policy | `mcp/omarchy_hardware/gpio_ssh.py` uses `StrictHostKeyChecking=accept-new`. |
| SSH allowlist | `policy.check_host()` requires an exact match in `Config.pi_hosts`; there is no host-string validation beyond non-empty strings in `config.py`. |
| GPIO pins | The default is BCM 2-27, but `config.py` currently accepts custom pins 0-27, so a custom config can re-enable BCM 0 and 1. |
| Configuration limits | `ssh_timeout`, serial limits, and flash configuration are converted with `int()`/`bool()` and do not currently enforce bounds or strict TOML types. |
| Upload token | `flash.py` binds the HMAC token to the resolved sketch directory, FQBN, and expiry. It does not bind the token to a board identity. |
| Upload preflight | `server.upload_sketch()` rejects an unrecognised board, but it does not compare the current board identity with identity observed during compilation. |
| Upload audit | `_log_upload()` catches `OSError` and returns no status, so audit-log failure is silent. |
| Existing tests | Tests cover policy path handling, token binding, serial sessions, and GPIO output parsing. There are no config-loading tests or SSH command-construction tests. |
| Existing validation | `CONTRIBUTING.md` lists pytest, Ruff, ShellCheck, manifest/version checks, and `omarchy plugin validate` for Omarchy. |

## Non-goals and unresolved decisions

These must not be guessed during implementation:

1. **SSH trust configuration:** decide whether the project will require a
   pre-populated `known_hosts` entry or add a per-host fingerprint field to
   `config.toml`. The plan does not choose a fingerprint format yet.
2. **Configuration bounds:** choose values for timeout and byte limits based on
   documented compatibility requirements before adding tests. Tests must encode
   the chosen contract, not an arbitrary limit.
3. **Board identity strength:** USB VID/PID and serial data are available when
   sysfs provides them, but identity is not proof that the physical board has
   not been swapped. The implementation must document what identity guarantee is
   actually provided.
4. **Audit-log failure policy:** decide whether an upload should be blocked when
   its log cannot be written, or whether the result should succeed with a
   prominent structured warning. Do not silently choose either behavior.

These decisions were made for the current implementation on 2026-09-11:

- Require an existing `known_hosts` entry; do not add a fingerprint field.
- Use conservative bounds: SSH timeout 1-60 seconds, per-call writes 1-4096
  bytes, and rolling write budget 1-65536 bytes/minute.
- Block an upload when the audit log cannot be prepared.
- Recheck the connected recognised board's suggested FQBN at upload time.

## Implementation phases

### Phase 0 - Establish a baseline

Run the existing commands from `CONTRIBUTING.md` and record exact results in the
intervention log:

```bash
./.dev-venv/bin/python -m pytest mcp/tests -q
./.dev-venv/bin/ruff check mcp/omarchy_hardware mcp/tests
shellcheck --severity=style bin/setup.sh bin/doctor.sh bin/scan-boards.sh bin/hardware-mcp
python3 .github/scripts/check_manifest.py
python3 .github/scripts/check_versions.py
```

On an Omarchy machine, also run:

```bash
omarchy plugin validate .
```

If a dependency or platform command is unavailable, record that fact rather than
claiming the check passed.

### Phase 1 - Close the SSH trust gap

**Files:** `mcp/omarchy_hardware/gpio_ssh.py`, `mcp/omarchy_hardware/config.py`,
`mcp/omarchy_hardware/policy.py`, `mcp/tests/`.

1. Decide and document the host-key verification contract.
2. Implement that contract without changing the fixed-command design.
3. Validate configured hosts for the characters and forms prohibited by the
   chosen SSH contract. Preserve support for any valid hostname/IP forms already
   required by the project; do not reject IPv6 or aliases without a documented
   reason.
4. Add unit tests that inspect the exact SSH arguments passed to
   `subprocess.run()`, including host-key options, timeout, `shell=False`, and
   the fixed remote command.
5. Add tests for empty, duplicate, malformed, and control-character host values.
6. Document the interaction with the user's SSH configuration and
   `known_hosts`.

The existing prohibition on arbitrary remote commands remains unchanged.

### Phase 2 - Make configuration validation explicit

**Files:** `mcp/omarchy_hardware/config.py`, `mcp/omarchy_hardware/policy.py`,
`mcp/tests/test_config.py` (new), `mcp/tests/test_policy.py`, `bin/setup.sh`,
`README.md`, `SECURITY.md`, `docs/threat-model.md`.

1. Define the accepted TOML types and bounds before coding.
2. Reject custom GPIO lists containing BCM 0 or 1, matching the stated safety
   policy and the default configuration.
3. Reject duplicates and invalid pin values.
4. Replace permissive `bool(...)` conversion for `flash.allow` with strict
   boolean validation.
5. Validate timeout and serial byte limits against the chosen positive bounds.
6. Preserve mode `600` checking for `config.toml`.
7. Add tests using temporary config paths and file modes; cover missing config,
   valid config, wrong TOML types, unsafe permissions, duplicate pins, forbidden
   pins, zero, negative, and oversized values.
8. Ensure setup-generated configuration remains valid under the new parser.

### Phase 3 - Make upload auditing non-silent

**Files:** `mcp/omarchy_hardware/flash.py`, `mcp/omarchy_hardware/errors.py`,
`mcp/tests/test_flash.py`, `README.md`, `SECURITY.md`.

1. Choose the audit-log failure policy from the unresolved decisions above.
2. Make `_log_upload()` return an explicit result or raise a specific
   application error; never discard `OSError`.
3. Create state directories/files with restrictive permissions and test the
   resulting modes where the platform supports them.
4. Keep both successful and failed upload attempts auditable.
5. Add tests for writable, unavailable, and read-only log locations.
6. Ensure the MCP response clearly distinguishes upload failure from audit-log
   failure.

### Phase 4 - Strengthen board preflight for flashing

**Files:** `mcp/omarchy_hardware/boards.py`,
`mcp/omarchy_hardware/ids.py`, `mcp/omarchy_hardware/flash.py`,
`mcp/omarchy_hardware/server.py`, `mcp/tests/test_flash.py`, and a new board
identity test module if needed.

1. Enumerate which board fields are reliably available: current code exposes
   port, VID, PID, serial, and board type.
2. Decide which of those fields can be captured in a compile authorization
   record without pretending they are cryptographic identity.
3. Bind the authorization to the selected identity data, if the decision
   supports it.
4. Re-enumerate immediately before upload and reject a changed, missing, or
   newly unknown board with an actionable error.
5. Preserve path revalidation, `confirm=True`, the short token lifetime, and
   serial-session restoration behavior.
6. Add tests for same board, replaced board, disconnected board, unknown board,
   changed sketch/FQBN, expired token, and upload failure.

Do not bind authorization to a field that is not available consistently on the
supported boards; document the residual risk instead.

### Phase 5 - Physical validation

**Files:** new `docs/hardware-validation.md`, plus any narrowly scoped fixes
discovered by testing.

Test with hardware, not a simulator:

1. Board discovery for each supported board family represented in `ids.py`.
2. Serial open/read/write/query/close and reconnect behavior.
3. A real compile and upload, including board replacement and upload failure.
4. A real Raspberry Pi using the configured SSH host, both GPIO backends where
   available, and a safe test pin.
5. Permission failures, disconnected devices, and malformed remote output.

Record exact hardware models, OS versions, tool versions, commands, outcomes,
and known limitations. Do not mark this phase complete from PTY tests alone.

### Phase 6 - Documentation, CI, and release

1. Update `README.md`, `SECURITY.md`, and `docs/threat-model.md` to match the
   implemented behavior and remaining residual risks.
2. Add a `CHANGELOG.md` entry for every behavior change, as required by
   `CONTRIBUTING.md`.
3. Keep tests and audits in `.github/workflows/ci.yml`; add only checks that
   correspond to implemented behavior.
4. Run the full local baseline again.
5. Run `omarchy plugin validate .` on Omarchy.
6. Update all three version locations together if behavior is released:
   `manifest.json`, `mcp/pyproject.toml`, and
   `mcp/omarchy_hardware/__init__.py`.
7. Release only after the physical validation results are documented.

## Definition of done

- The selected SSH host-key policy is explicit, tested, and documented.
- Invalid configuration is rejected with actionable errors.
- BCM 0 and 1 cannot be enabled through custom configuration unless the safety
  policy is deliberately changed and documented.
- Upload audit failures are visible and follow the chosen blocking/warning policy.
- Board changes between authorization and upload are handled according to the
  documented identity guarantee.
- Existing security gates remain intact.
- Unit, shell, manifest, version, security, and Omarchy validation results are
  recorded.
- Physical test results are present before release claims are made.

## Intervention protocol

For each implementation commit:

1. Add the intended scope and unresolved decisions to the collaboration log
   before editing.
2. Implement one phase or a clearly bounded sub-phase.
3. Update the relevant checklist item only after validation passes.
4. Record exact commands, results, skipped checks, and limitations.
5. Commit and push the code and the log together.

| Date | Actor/model | Phase | Change | Validation | Commit |
|------|-------------|-------|--------|------------|--------|
| 2026-09-11 | Copilot CLI | Planning | Inspected current source, tests, setup script, CI, and contributor rules; created this implementation plan without selecting unresolved policy values. | Repository inspection completed; no implementation claimed. | TBD |
