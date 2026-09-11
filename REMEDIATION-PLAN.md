# Remediation Plan

This document tracks the security and reliability remediation work for
`omarchy-hardware`. It is intentionally versioned so that planning, model
interventions, implementation decisions, and validation remain visible in Git.

## Goals

- Prevent unintended SSH trust decisions and constrain remote targets.
- Reject unsafe or ambiguous configuration instead of coercing it.
- Make firmware flashing auditable and harder to direct at the wrong board.
- Eliminate silent failures in security-relevant logging.
- Validate destructive hardware operations on physical devices.

## Action plan

### P0 - SSH trust and scope

- [x] Replace `StrictHostKeyChecking=accept-new` with explicit host-key
      verification.
- [x] Decide whether approved host fingerprints should be supported in the
      configuration; if so, validate and document their format.
- [x] Validate configured host strings and reject control characters, whitespace,
      empty values, duplicates, and unsupported forms.
- [x] Add tests proving unknown or unapproved SSH targets are rejected.
- [x] Document the effect of `~/.ssh/config`, aliases, DNS, and host-key
      changes on the trust model.

### P1 - Configuration and auditability

- [x] Validate SSH timeout, serial write limits, and write budgets against
      positive bounded ranges.
- [x] Reject duplicate or invalid GPIO pins and require strict boolean values
      for flash configuration.
- [x] Replace silent upload-log failures with an explicit structured warning or
      error.
- [x] Ensure upload log directories and files are created with restrictive
      permissions.
- [x] Add tests for invalid configuration and unavailable or read-only audit
      logs.

### P1 - Flash safety

- [x] Bind the compile/upload authorization to the intended board identity when
      reliable identity data is available.
- [x] Re-enumerate and revalidate the board immediately before upload.
- [x] Test board replacement, disconnects, upload failures, and serial-session
      recovery.
- [x] Keep the short-lived compile token and explicit confirmation requirement.

### P1 - Physical validation

- [ ] Test serial discovery, read/write, and recovery with supported boards.
- [ ] Test real firmware upload on at least one supported board.
- [ ] Test Raspberry Pi status, pin reads, mode changes, and writes on a real
      Pi.
- [ ] Record hardware, software versions, commands, results, and limitations in
      `docs/hardware-validation.md`.

### P2 - Installation, CI, and documentation

- [x] Add a setup `--dry-run` mode.
- [x] Check required external commands before making changes.
- [x] Keep Python, shell, dependency, workflow, and manifest checks in CI.
- [x] Add regression tests for every remediation item.
- [x] Update the threat model, security policy, README, and changelog.
- [ ] Publish a release only after the relevant physical validation is complete.

## Implementation order

1. SSH host-key verification and host validation.
2. Strict configuration validation.
3. Non-silent upload logging.
4. Board-bound upload authorization and pre-upload revalidation.
5. Unit and regression tests.
6. Physical hardware validation.
7. Documentation and release.

## Completion criteria

- Unknown SSH host keys are not accepted automatically.
- Invalid configuration is rejected with actionable errors.
- Every upload attempt is auditable or reports that audit logging failed.
- A board change between compilation and upload cannot silently redirect the
  operation.
- Serial and GPIO behavior has been verified on physical hardware.
- CI passes with regression coverage for each changed security boundary.

## Collaboration and intervention log

Record every meaningful intervention by a human or model in chronological order.
Each entry should reference the commit that contains the change.

| Date | Actor/model | Scope | Decision or change | Validation | Commit |
|------|-------------|-------|--------------------|------------|--------|
| 2026-09-11 | Copilot CLI | Initial review | Created this remediation plan from the repository security posture review. | Repository contents and threat model reviewed. | 1dfaa10 |
| 2026-09-11 | Copilot CLI | Implementation planning | Added the evidence-based action and implementation plan in `IMPLEMENTATION-PLAN.md`. | Current source, tests, setup script, CI, and contributor rules inspected; unresolved policy choices left explicit. | 954c6b7 |
| 2026-09-11 | Copilot CLI | P0/P1 implementation | Applied strict `known_hosts` SSH verification, conservative config validation, blocking audit-log preflight, restrictive audit permissions, and runtime board/FQBN revalidation. | 58 changed-area tests passed; Ruff, manifest, version, and diff checks passed. Full serial test run stalled in an existing PTY write test on macOS and was stopped. | e93fe7f |
| 2026-09-11 | Grok | F0–F4, F3, F5 template | Discarded unused `pi.host_keys` parser (trust stays in `known_hosts`). Removed dead `Config.pi_host_keys`. Serial write no longer `flush()`/`tcdrain()` (macOS PTY hang). Upload reports `session_restored`. SSH argv uses `--` before the host; `check_host` runs inside `_run`. SSH argv tests, upload preflight tests, `setup.sh --dry-run` + python3 preflight, portable plugin-dir resolution, Panel.qml quotes the setup path, `docs/hardware-validation.md` template. `--dry-run` is covered by pytest (`test_setup.py`); the token cannot push workflow-file edits. | 90 pytest passed on Darwin/py3.11; ruff passed; `bash -n` on all bin scripts; `setup.sh --dry-run` mutated nothing. `omarchy plugin validate` and `shellcheck` skipped (not on this Mac). | 2b632ef |
| 2026-09-11 | Grok | Support matrix | Added `docs/support-matrix.md`, `list_capabilities`, `UNSUPPORTED_OPERATION`, and `operations` on capability records. Pi GPIO/PWM/SPI/I2C, Jetson, and Siemens rows stay explicit; empty validation still means experimental. | 95 pytest passed; native C test passed; C# build skipped because no .NET SDK is installed. | Working tree integration |
| 2026-09-11 | Grok | Schneider + Weintek | Added `schneider` Modicon PLC and `weintek_hmi` families as unsupported typed contracts. No Modbus/UMAS/EasyAccess implementation. | pending | pending |
| 2026-09-11 | Grok | Weintek OPC UA/MQTT | Weintek transports are OPC UA and MQTT with exact endpoint/node/topic allowlists. Tools enforce the allowlist then return UNSUPPORTED_OPERATION until a live client is wired. | pending | pending |

## Change protocol for multiple models

1. Read this document before modifying the repository.
2. Add an entry to the collaboration log describing the intended change.
3. Implement only the scoped change and update the relevant checklist item.
4. Run the smallest applicable existing validation commands.
5. Record the validation result and commit hash in the log.
6. Push the commit so subsequent models can inspect the complete history.
