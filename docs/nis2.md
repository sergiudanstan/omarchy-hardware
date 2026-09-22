# NIS2 evidence

[`mcp/hardware_validation/run_nis2.py`](../mcp/hardware_validation/run_nis2.py) collects
technical evidence that omarchy-hardware's controls support the cybersecurity
risk-management measures of the NIS2 Directive (EU) 2022/2555, Article 21(2), and
the incident reporting of Article 23.

## What this is, and what it is not

NIS2 places obligations on **essential and important entities**. It does not apply to
a software product as such. An organisation that uses this plugin in its operations
can use the evidence below for its own Article 21 measures. The run does **not**
certify anyone as compliant, and it does not replace the organisation's policies,
training or reporting processes.

The EU law that addresses products with digital elements is the Cyber Resilience
Act (EU) 2024/2847. It is out of scope here.

Every check ends in one of four states:

| Status | Meaning |
|---|---|
| `pass` | The control was exercised and held. |
| `fail` | The control was exercised and did not hold. The run exits non-zero. |
| `not_applicable` | The measure does not apply to a single-user desktop plugin. |
| `limitation` | A known gap, stated rather than hidden. |

## Running it

The run reads the real `config.toml` and audit log, and talks to the local MING
stack and to GitHub (`gh` must be logged in). It changes nothing except temporary
files. Run the Arduino runners first, so their results can be attached:

```bash
python mcp/hardware_validation/run_nis2.py \
  --plugin-dir ~/.config/omarchy/plugins/io.github.sergiudanstan.hardware \
  --repo . --release v0.1.5 \
  --uno-results uno.json uno-extended.json --out nis2.json
```

Use the plugin's own virtualenv Python. The test suite and the claims check run with
the repository's `.dev-venv`.

## Mapping

| ID | Article | Measure | Control exercised |
|---|---|---|---|
| NIS2-A-1 | 21(2)(a) | Risk analysis and security policies | A threat model and a security policy ship with the release |
| NIS2-A-2 | 21(2)(a) | Risk analysis and security policies | The threat model's claims (for example the test count) match the code |
| NIS2-B-1 | 21(2)(b) | Incident handling | The hash-chained audit log verifies end to end |
| NIS2-B-2 | 21(2)(b) | Incident handling | Editing one record, in a temporary copy, is detected at that line |
| NIS2-B-3 | 21(2)(b), 23 | Incident handling and reporting | Actuations are logged with time, process and outcome; every upload start has a finish |
| NIS2-B-4 | 21(2)(b) | Incident handling | A private vulnerability channel with response targets exists |
| NIS2-B-5 | 23 | Reporting obligations | The audit log holds only real events (test records are named if present) |
| NIS2-C-1 | 21(2)(c) | Business continuity | What was flashed to each board is recorded in the board journal |
| NIS2-C-2 | 21(2)(c) | Business continuity | The audit chain continues across server restarts and processes |
| NIS2-C-3 | 21(2)(c) | Backup management | ESP32 firmware can be backed up and restored (restore needs confirmation) |
| NIS2-C-4 | 21(2)(c) | Backup management | `config.toml` has no built-in backup (limitation) |
| NIS2-D-1 | 21(2)(d) | Supply chain security | Every runtime dependency is pinned and hash-locked |
| NIS2-D-2 | 21(2)(d), (e) | Supply chain; vulnerability handling | `pip-audit` finds no known vulnerability in the lock |
| NIS2-D-3 | 21(2)(d) | Supply chain security | Every CI action is pinned to a commit SHA |
| NIS2-D-4 | 21(2)(d), (e) | Supply chain; vulnerability handling | Dependabot updates pip and GitHub Actions dependencies |
| NIS2-D-5 | 21(2)(d) | Supply chain security | The release ships a CycloneDX SBOM |
| NIS2-D-6 | 21(2)(d) | Supply chain security | `gh attestation verify` passes for the plugin archive and the wheel |
| NIS2-E-1 | 21(2)(e) | Secure development | `main` requires signed commits, the CI checks and resolved review threads |
| NIS2-E-2 | 21(2)(e) | Secure development | The release tag is signed |
| NIS2-E-3 | 21(2)(e) | Vulnerability handling | No open CodeQL findings |
| NIS2-E-4 | 21(2)(e) | Secure development | CI is green on `main` |
| NIS2-E-5 | 21(2)(e) | Vulnerability disclosure | A coordinated disclosure policy (90 days) is published |
| NIS2-E-6 | 21(2)(e), (f) | Secure development; effectiveness | Open OpenSSF Scorecard items are listed (limitation for a single maintainer) |
| NIS2-F-1 | 21(2)(f) | Assessing effectiveness | The automated test suite passes |
| NIS2-F-2 | 21(2)(f) | Assessing effectiveness | Physical validation on real hardware passes |
| NIS2-G-1 | 21(2)(g) | Cyber hygiene | `config.toml` is private, owned by the user and not a symlink |
| NIS2-G-2 | 21(2)(g) | Cyber hygiene | A group-readable or symlinked config is refused |
| NIS2-G-3 | 21(2)(g) | Cyber hygiene | Every credential file named in the config is private |
| NIS2-G-4 | 21(2)(g) | Cyber hygiene | No secret is stored inline; only `*_env` / `*_file` references |
| NIS2-H-1 | 21(2)(h) | Cryptography | Upload authorisation is an HMAC token that cannot be forged or reused for another board |
| NIS2-H-2 | 21(2)(h), (j) | Cryptography; secured communications | MQTT over TLS with the pinned CA connects |
| NIS2-H-3 | 21(2)(h), (j) | Cryptography; secured communications | TLS refuses an unknown CA and a wrong hostname |
| NIS2-H-4 | 21(2)(h), (j) | Cryptography; secured communications | HTTPS to InfluxDB verifies against the pinned CA and refuses the system store |
| NIS2-H-5 | 21(2)(h) | Cryptography | TLS 1.2 is the minimum protocol version |
| NIS2-I-1 | 21(2)(i) | Access control | Only allowlisted serial devices can be opened, including after symlink resolution |
| NIS2-I-2 | 21(2)(i) | Access control | Compile and upload stay inside `sketch_roots` |
| NIS2-I-3 | 21(2)(i) | Access control | Remote hosts outside the allowlist are refused |
| NIS2-I-4 | 21(2)(i) | Access control | Writes are rate-limited per target |
| NIS2-I-5 | 21(2)(i) | Access control | Confirmation gates and USB-serial redaction hold on the real board |
| NIS2-I-6 | 21(2)(i) | Asset management | Connected hardware is inventoried |
| NIS2-I-7 | 21(2)(i) | Human-resources security | Not applicable to a desktop plugin |
| NIS2-J-1 | 21(2)(j) | Secured communications | Cleartext MQTT to a remote host needs an explicit waiver |
| NIS2-J-2 | 21(2)(j) | Multi-factor authentication | The maintainer's GitHub account uses 2FA |
| NIS2-J-3 | 21(2)(j) | Multi-factor authentication | Not applicable: a local stdio server has no network login |

`mcp/tests/test_nis2_mapping.py` fails if a check is added to the runner without a
row here, or a row is left behind after a check is removed.

## Recorded runs

- [2026-09-22](hardware-validation/nis2-2026-09-22.json), against the installed v0.1.5 with the
  Arduino Uno attached.
