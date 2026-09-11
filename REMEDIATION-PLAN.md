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

- [ ] Replace `StrictHostKeyChecking=accept-new` with explicit host-key
      verification.
- [ ] Decide whether approved host fingerprints should be supported in the
      configuration; if so, validate and document their format.
- [ ] Validate configured host strings and reject control characters, whitespace,
      empty values, duplicates, and unsupported forms.
- [ ] Add tests proving unknown or unapproved SSH targets are rejected.
- [ ] Document the effect of `~/.ssh/config`, aliases, DNS, and host-key
      changes on the trust model.

### P1 - Configuration and auditability

- [ ] Validate SSH timeout, serial write limits, and write budgets against
      positive bounded ranges.
- [ ] Reject duplicate or invalid GPIO pins and require strict boolean values
      for flash configuration.
- [ ] Replace silent upload-log failures with an explicit structured warning or
      error.
- [ ] Ensure upload log directories and files are created with restrictive
      permissions.
- [ ] Add tests for invalid configuration and unavailable or read-only audit
      logs.

### P1 - Flash safety

- [ ] Bind the compile/upload authorization to the intended board identity when
      reliable identity data is available.
- [ ] Re-enumerate and revalidate the board immediately before upload.
- [ ] Test board replacement, disconnects, upload failures, and serial-session
      recovery.
- [ ] Keep the short-lived compile token and explicit confirmation requirement.

### P1 - Physical validation

- [ ] Test serial discovery, read/write, and recovery with supported boards.
- [ ] Test real firmware upload on at least one supported board.
- [ ] Test Raspberry Pi status, pin reads, mode changes, and writes on a real
      Pi.
- [ ] Record hardware, software versions, commands, results, and limitations in
      `docs/hardware-validation.md`.

### P2 - Installation, CI, and documentation

- [ ] Add a setup `--dry-run` mode.
- [ ] Check required external commands before making changes.
- [ ] Keep Python, shell, dependency, workflow, and manifest checks in CI.
- [ ] Add regression tests for every remediation item.
- [ ] Update the threat model, security policy, README, and changelog.
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
| 2026-09-11 | Copilot CLI | Implementation planning | Added the evidence-based action and implementation plan in `IMPLEMENTATION-PLAN.md`. | Current source, tests, setup script, CI, and contributor rules inspected; unresolved policy choices left explicit. | TBD |

## Change protocol for multiple models

1. Read this document before modifying the repository.
2. Add an entry to the collaboration log describing the intended change.
3. Implement only the scoped change and update the relevant checklist item.
4. Run the smallest applicable existing validation commands.
5. Record the validation result and commit hash in the log.
6. Push the commit so subsequent models can inspect the complete history.
