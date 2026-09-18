# Security Policy

This plugin runs **unsandboxed inside the Omarchy shell process**, talks to physical serial
devices, and can open SSH connections to a Raspberry Pi. Its security posture is therefore
worth stating explicitly rather than leaving implicit.

## Supported versions

| Version | Supported |
|---|---|
| 0.1.x | ✅ Current |

This is a young project with a single maintainer. Only the latest release receives fixes;
there are no backports.

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Report privately through GitHub:
**[Report a vulnerability](https://github.com/sergiudanstan/omarchy-hardware/security/advisories/new)**
(Security → Advisories → Report a vulnerability).

That channel is private between you and the maintainer, supports a coordinated fix, and can
issue a CVE if one is warranted.

### What to expect

| Stage | Target |
|---|---|
| Acknowledgement | within 3 business days |
| Initial triage and severity assessment | within 10 business days |
| Fix released, or a documented decision not to fix | within 90 days |

**These are best-effort targets from a single unpaid maintainer, not a contractual SLA.**
Saying so plainly is more useful than publishing a commitment that cannot be honoured. If a
target slips you will be told, rather than left waiting.

There is no bug bounty. Credit is offered in the advisory and changelog unless you prefer to
stay anonymous.

## Coordinated disclosure

Default embargo is **90 days** from acknowledgement, or until a fix ships — whichever comes
first. If a vulnerability is being actively exploited, that window shortens and disclosure
is expedited. Extensions are possible by agreement if a fix is genuinely complex.

## Scope

### In scope — these are the things worth attacking

The security model assumes **the language model is untrusted input**. Any tool argument may
have been chosen adversarially. Reports that defeat one of these boundaries are in scope:

- **Serial device allowlist bypass** (`mcp/omarchy_hardware/policy.py`) — reaching any path
  other than `/dev/ttyACM*`, `/dev/ttyUSB*` or `/dev/serial/by-id/*`. The path is re-checked
  *after* `realpath`; a way around that re-check is a valid finding.
- **Upload token forgery** (`mcp/omarchy_hardware/flash.py`) — flashing firmware without a
  token minted by a real compile in the same process, or without `confirm=true`.
- **Command or argument injection over SSH** (`mcp/omarchy_hardware/gpio_ssh.py`) — causing
  anything other than the fixed `pinctrl` / `raspi-gpio` verbs to run on the Pi, or reaching
  a host that is not in the configured allowlist.
- **SSH host-key trust and session isolation** — the configured host's key must already
  be present in `known_hosts`; the plugin does not automatically trust a new key, and
  must not inherit agent forwarding, X11, `ProxyCommand`, or `PermitLocalCommand` from
  `~/.ssh/config`.
- **Allowlist leakage** — tool errors and `hardware_report` must not return the contents
  of `[pi] hosts` or Weintek allowlists to the model.
- **Configuration trust failures** (`mcp/omarchy_hardware/config.py`) — the config file being
  honoured despite unsafe permissions, or allowlists being bypassed.
- **Privilege escalation** through `bin/setup.sh` beyond the single documented
  `usermod -aG uucp`.
- **Audit chain forgery** (`mcp/omarchy_hardware/audit.py`) — appending, editing or removing a
  record that `audit_status` still reports as intact, or an actuation reaching hardware without
  a record preceding it.
- **Transport downgrade** (`mcp/omarchy_hardware/config.py`, `policy.py`) — reaching an OPC UA
  or MQTT target unsigned, unencrypted or in cleartext without `allow_insecure` being set for
  exactly that target.
- Anything that lets the MCP server write outside its intended surface, or that leaks the
  contents of `config.toml`.

### Out of scope

- Physical access to the machine or to a connected board.
- The user's own `sudo` rights, or a user deliberately allowlisting a device or host.
- Vulnerabilities in upstream projects (`arduino-cli`, Quickshell, Omarchy, `pyserial`, `asyncua`, the
  MCP SDK) — report those upstream; tell us if this plugin makes one materially worse.
- A compromised Raspberry Pi at the far end of an SSH session that the user chose to trust.
- The fact that Omarchy plugins run unsandboxed. That is a property of the plugin system and
  is documented, not a defect in this project.

## Security posture

Design detail lives in [`docs/threat-model.md`](docs/threat-model.md). In summary: an argv-only
SSH layer with no arbitrary-remote-command tool, a device allowlist re-validated after symlink
resolution, strict existing-host-key checking, double-gated firmware flashing, a `0600` config
file, a hash-chained audit log that must accept a record before anything moves, secure-by-default
transport for industrial endpoints, and exactly one privileged operation performed interactively
by the user.

CI runs the test suite on every push, plus `ruff` (including the flake8-bandit ruleset),
`shellcheck`, `pip-audit` against both the runtime lockfile and the CI toolchain, and `zizmor`
to audit the workflows themselves. GitHub Actions are pinned to full commit SHAs. A `documented
claims` job checks that the countable claims in the threat model still match the repository, so
the evidence offered below cannot quietly go stale.

Releases publish a CycloneDX SBOM and Sigstore build provenance for every artifact, including an
archive of the plugin tree itself — the marketplace installs this repository rather than the
wheel, so attesting only the wheel would be provenance for something nobody runs:

```
gh attestation verify omarchy-hardware-v0.1.2-plugin.tar.gz --repo sergiudanstan/omarchy-hardware
```

## What this project does not claim

This project targets the **OpenSSF Open Source Project Security Baseline, Level 1**.

It is **not** certified against any regulation. In particular it makes no claim of NIS2 or Cyber
Resilience Act compliance — NIS2 regulates organisations rather than software and has no
certification scheme, and this is an open-source project maintained outside commercial activity.
If you are a regulated entity assessing this plugin as a third-party component under NIS2
Art. 21(2)(d)/(e), the threat model, this policy, and the CI results are the evidence offered;
your own assessment remains yours to make.
