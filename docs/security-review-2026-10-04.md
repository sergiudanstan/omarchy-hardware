# Security review — 2026-10-04

Reviewed baseline: `7dc447786e0a9e2810d5d5772a4d74e0f9c8a682`.
This update addresses HANCORE-linux's second marketplace finding and expands the
review across the repository. It is a source review with regression testing,
not a penetration-test certification or a guarantee that no vulnerabilities remain.

## Confirmed findings and changes

| Area | Trigger and consequence | Change and regression evidence |
| --- | --- | --- |
| MING validator credentials | InfluxDB admin and Node-RED tokens appeared in curl argv, visible to other local users on normal Linux hosts. | Both headers travel through stdin using `-H @-`. Curl ignores ambient configuration/proxies. Tests inspect constructed calls; a native curl test checks received headers/body, ignores a hostile `.curlrc`, and requires `/proc/<pid>/{cmdline,environ}` inspection on Linux. |
| HTTP error disclosure | An authenticated service could reflect a credential or fragment in its error body; malformed environment credentials could appear in urllib header exceptions. | Authenticated error bodies are omitted; invalid HTTP headers return a generic error; environment secrets have the same line/size restrictions as file secrets. Tests cover full/partial reflection and malformed input. |
| Reused bootstrap secrets | `umask` did not repair existing world-readable files/directories; symlinks/special files could be reused. | Before reuse, reset managed file/directory permissions and ACLs, reject symlinks, special files and foreign owners, then apply service ACL grants. Executable preflight tests use disposable files and stub Docker/OpenSSL/ACL commands. Actual Linux ACL grants still need live-stack validation. |
| Private-file availability | Opening a FIFO blocked before regular-file checks could reject it. | Config, parts and credential opens are nonblocking, then checked with `fstat`. Subprocess tests require rejection within a deadline. This is local misconfiguration/availability hardening, not a demonstrated privilege escalation. |
| Network availability | MQTT retained messages before SUBACK could grow without a count cap and exceed the requested collection limit. WebSocket trickle/ping traffic restarted per-read timeouts; malformed control frames were accepted. | Bound early MQTT messages to 32 and preserve collection limits. Enforce absolute WebSocket receive/handshake deadlines, header limits and control-frame shape. Adversarial broker and deterministic trickle/ping tests reproduce these cases. |
| Slack subprocess lifetime | A request timeout killed Claude's parent process but could leave MCP/compile descendants running. | Reuse process-group cleanup; a native child-process test verifies that a delayed child cannot write after timeout. |
| False validation passes | Failed Docker/curl probes, missing containers, wrong service images, failed tag verification, partial release downloads, empty audit evidence and incomplete historical reports could support success claims. | Require successful probes, expected services and per-service image digests, valid Flux CSV/HTTP evidence, native exit statuses and nonempty evidence. Legacy hardware JSON is explicitly a limitation because it lacks verified provenance and a complete check manifest. Unknown check statuses fail. |
| Assurance claims | The local unkeyed audit chain was described as detecting any rewrite. CI installed idna 3.19 over runtime's pinned 3.20. | Document undetectable tail truncation/recomputed chains; align the CI dependency pin with runtime. The hash chain remains an internal consistency check. |

Curl's stdin-header behavior is documented in the [curl manual](https://curl.se/docs/manpage.html#-H).

## Review scope

The repository-wide pass searched execution, credential, authentication, file and
network boundaries across Python runtime and validation modules, `bin/`, example
scripts/configuration, QML/JavaScript launch surfaces, native C, .NET contracts and
GitHub workflows. Manual tracing concentrated on model-supplied arguments,
allowlists, confirmation/audit gates, subprocess construction, secret readers,
HTTP/MQTT/WebSocket/SSH/OPC UA paths, upload artifacts, and validation evidence.
Existing tests also exercise path traversal, upload-token forgery, transport
refusals, malformed protocol data and confirmation gates. No new shell-injection
or upload-token bypass was confirmed in this pass.

A targeted scan of 215 tracked text files found no private-key blocks or tokens
matching the selected GitHub, AWS and Slack credential patterns. This scan does
not cover arbitrary opaque secrets, Git history, untracked deployment files,
running containers, or credentials in external logs. Ruff security rules and
CodeQL provide additional static analysis; neither establishes functional safety.

## Verification

- 58 regression cases added; the suite collects **873 tests**.
- **35 known-failure cases fail against a temporary copy of the baseline** using
  the new tests and public fixtures. These include both credential-argv exposure
  and validation false negatives. Production artifacts were not modified by the probes.
- Local Python 3.12.12/pytest 9.1.1: **872 passed, one failed**. The failure is the
  existing Linux `/proc` descriptor test on macOS, where `/proc` is unavailable.
  It is not reported as a pass or silently skipped. Linux CI is required for the
  target-platform result and the new native curl `/proc` checks.
- Native curl 8.7.1 transport regression, native C tests and the three existing
  greenhouse credential tests passed locally. Ruff passed. Runtime and CI
  lockfile audits completed with no known vulnerabilities reported.
- Zizmor 1.30.1's offline workflow scan reported no findings under its enabled
  rules, with seven findings suppressed by its defaults. Online repository-setting
  checks are outside that result.

Raw local commands, exit statuses, pytest XML, baseline-failure output, dependency
reports and tool versions are retained in the maintainer's
`codex-tests/omarchy-security-evidence-20261004/` directory. The associated PR's
GitHub checks provide public Linux evidence tied to its commit.

## Remaining boundaries and operational follow-up

No real MING stack, USB board, PLC/HMI, physical actuation or Slack account was
revalidated in this pass. Historic JSON reports were preserved unchanged and do
not validate this update. Docker image vulnerabilities and live service settings
need a separate deployment audit; Python dependency scans do not cover them.

The bootstrap uses explicit service-UID ACL grants (1883, 1000 and 472). A host
account with a granted UID receives that service's access. Same-user/root access,
trusted configured build tools and a compromised configured remote host remain
important trust boundaries. HTTP response byte caps/socket timeouts do not provide
a universal end-to-end wall-clock guarantee against a trickling trusted service.

If the old validator ran on a shared host, revoke/replace its exposed InfluxDB
admin and Node-RED static tokens and restart/reconfigure affected services.
Changing code cannot revoke previously copied credentials. Re-run the updated
bootstrap before demo setup to repair existing managed file permissions; bootstrap
preserves credential values and does not rotate them. Password examples now use
a hidden prompt instead of putting a literal password in shell history.
