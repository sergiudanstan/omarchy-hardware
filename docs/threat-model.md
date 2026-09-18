# Threat model

Written for someone deciding whether to allow this plugin on a managed machine. It states what
the plugin can do, what constrains it, and what it does not defend against.

## What this is

Two components shipped together:

1. **An MCP server** (Python) that Claude Code launches over stdio. It exposes tools for USB
   serial I/O, compiling and flashing Arduino sketches, Raspberry Pi GPIO over SSH, reading
   and writing a MING stack (MQTT, InfluxDB, Node-RED, Grafana) over the network, and
   querying the hardware support matrix. Unimplemented families return
   `UNSUPPORTED_OPERATION`.
2. **A Quickshell bar widget** (QML) that lists connected boards and reports setup state.

## Trust boundaries

```
┌── Claude (UNTRUSTED INPUT) ────────────────────────────────────┐
│  Tool arguments may be adversarial: prompt injection from a    │
│  web page, a repo, or serial output the model just read.       │
└───────────────────────┬────────────────────────────────────────┘
                        │ MCP over stdio, local pipe, no network
┌───────────────────────▼────────────────────────────────────────┐
│  MCP server — user's UID, NOT root, unsandboxed                │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ policy.py — the single choke point                       │  │
│  │  · device allowlist, re-checked AFTER realpath           │  │
│  │  · SSH host allowlist   · BCM pin allowlist              │  │
│  │  · per-port write budget · per-pin actuation budget      │  │
│  │  · transport security for OPC UA / MQTT targets          │  │
│  │  · MING targets, topics, buckets, inject nodes by name   │  │
│  └──────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ audit.py — chained log; an actuation that cannot be      │  │
│  │ recorded is refused, not performed                       │  │
│  └──────────────────────────────────────────────────────────┘  │
└───────┬─────────────────────────────────┬──────────────────────┘
        │ /dev/ttyACM* /dev/ttyUSB*       │ ssh, argv only, shell=False
┌───────▼───────────────┐         ┌───────▼───────────────────────┐
│ USB board (untrusted  │         │ Raspberry Pi (separate host,  │
│ output — may be junk  │         │ user-configured, key auth)    │
│ or hostile bytes)     │         │ fixed pinctrl/raspi-gpio/cat/ │
│                       │         │ df/vcgencmd verbs only        │
└───────────────────────┘         └───────────────────────────────┘

Separate process, no MCP access:
┌────────────────────────────────────────────────────────────────┐
│ QML bar widget inside omarchy-shell (unsandboxed, user's UID)   │
│ poll: bin/scan-boards.sh and bin/doctor.sh, no arguments        │
│ user-triggered: setup.sh in a terminal / editor (quoted path)   │
└────────────────────────────────────────────────────────────────┘
```

## The central assumption

**The language model is untrusted.** Every tool argument is treated as potentially
attacker-chosen — a model can be steered by a malicious web page, a poisoned repository, or
even bytes it just read off the serial port. No control decision depends on the model behaving
well. All of it is enforced in `policy.py`.

## Privileges

- **The MCP server never runs as root** and never invokes `sudo`. It runs as the user, and can
  do anything that user could do — it is not sandboxed, and neither is any Omarchy plugin.
- **One privileged operation exists**: `sudo usermod -aG uucp $USER` in `bin/setup.sh`, run
  interactively by the user. The panel opens it in a terminal instead of raising a graphical
  polkit prompt, specifically so the user reads the command before authorising it.
- **No udev rules, no systemd units, no sudoers entries** are ever written.

## Controls, and what each actually stops

| Control | Where | Stops |
|---|---|---|
| Device allowlist re-checked after `realpath` | `policy.resolve_port` | A symlinked or swapped `/dev/ttyACM0` redirecting writes to `/dev/sda` |
| `/dev/ttyS*` excluded | `policy.DEVICE_PATTERN` | Writing to a built-in UART that is often a serial console |
| Fixed argv, `shell=False`, `--` before host | `gpio_ssh._run` | Shell metacharacter injection into the Pi; `--` after the host would become the remote command |
| No arbitrary-remote-command tool exists | `gpio_ssh.py` | The whole class of "ask the model to run X on the Pi" |
| Host allowlist and existing host key | `policy.check_host`, `gpio_ssh._run`, `gpio_ssh.SSH_BASE` | Reaching a machine the user never authorised, passing a destination that starts with `-`, or trusting a new SSH key automatically |
| Separate `[jetson] hosts` | `jetson_ssh.py`, `config.py` | Running `pinctrl` on a Jetson, or mixing Pi and Jetson in one allowlist |
| Pin allowlist, BCM 0/1 excluded | `policy.check_pin` | Driving the HAT ID EEPROM pins |
| GPIO `confirm=true` | `gpio_set_mode`, `gpio_write_pin` | A single unconsidered tool call changing pin mode or level |
| Serial `confirm=true` | `serial_write`, `serial_query` | A single unconsidered tool call writing the serial port |
| Unknown adapters cannot be written | `server._require_serial_write_target` | Writing a CH340/generic USB-serial device unless `[serial] allow_unknown` |
| HMAC token + explicit `confirm` | `flash.py` | Firmware being overwritten in one unconsidered tool call |
| Verified private upload snapshot | `flash._artifact_snapshot` | A concurrent rebuild replacing firmware after the upload digest check |
| Token bound to USB serial when present | `flash.mint_token` | Flashing a swapped board that has a different USB serial |
| Sketch directory allowlist | `flash.resolve_sketch_dir` | Compiling or uploading a path outside `[flash] sketch_roots` |
| Unknown boards refuse to flash | `server.upload_sketch` | Flashing an unidentifiable device, including ambiguous Espressif `303a:1001` |
| Config refused if group/world-readable, not owned, or a symlink | `config._check_permissions` | Another local user editing the SSH host allowlist |
| SSH forwarding/proxy pinned off | `gpio_ssh.SSH_BASE` | Inheriting `ForwardAgent`, `ProxyCommand`, or `LocalCommand` from `~/.ssh/config` |
| Host allowlist not returned to the model | `policy.check_host`, `server._resolve_host` | Prompt injection reading `[pi] hosts` from error text |
| Bounded ring buffer, deadline on every read | `serial_session.py` | A silent or flooding device hanging or exhausting the session |
| Per-port rolling write budget | `policy.WriteBudget` | Sustained writes wearing flash or spamming a device |
| Per-pin rolling actuation budget | `policy.ActuationBudget` | A GPIO pin driven in a loop wearing a relay or contactor |
| Tamper-evident actuation log, refused if unwritable | `audit.require`, `audit.verify` | An actuation nobody can reconstruct afterwards, and a history quietly rewritten by anything running as the user |
| MING target, topic, bucket and inject-node allowlists | `policy.ming_*`, `policy.check_ming_*`, `config._parse_ming` | Reaching a broker, database, flow or dashboard the user did not name; a subscribe filter wider than the configured one (`filter_covers`); publishing to a wildcard or `$` topic |
| Typed InfluxDB queries and line protocol | `ming.build_query`, `ming.build_line` | Flux `to()`, `http.post` or `sql.from` through a "read" tool; a value that injects a second point, measurement or string interpolation |
| No Node-RED flow deploy | `server.py` (no tool), `support.py` (`ming.nodered.deploy` unsupported) | Function and exec nodes turning a flow deploy into a remote shell on the Node-RED host |
| MING transport: TLS unless loopback, no redirects, no proxies, bounded reads | `config._http_security`, `config._mqtt_security`, `policy._require_secure_http`, `http_lite`, `mqtt_lite` | An API token or MQTT password crossing a network in cleartext, being redirected to another host, or sent through a proxy from the environment; a service answering with an unbounded response or packet |
| MING credentials by reference only | `ming.read_secret` | Tokens in `config.toml`; a token file readable by other users or swapped for a symlink |
| MING writes: `confirm=true`, per-target budget, audit before sending | `server.mqtt_publish`, `influx_write`, `nodered_inject`, `grafana_annotate`, `policy.MingWriteBudget` | A single unconsidered call publishing to equipment; a loop flooding a topic or bucket; a write nobody can reconstruct |
| Weintek OPC UA/MQTT allowlists and transport security | `policy.check_weintek_opcua`, `check_weintek_mqtt`, `config._opcua_security`, `config._mqtt_security` | Contacting an HMI, node, or topic the user did not list; MQTT wildcards and OPC UA credentials in URLs; reaching an HMI unsigned, unencrypted or in cleartext without an explicit `allow_insecure`. **Not yet on a live path**: the tools return `UNSUPPORTED_OPERATION` for every argument, so these run in tests only until a client exists |

## Residual risks — accepted, not solved

- **Unsandboxed execution.** Both halves run with the user's full privileges. A code-execution
  bug here is as serious as one in any other program that user runs. This is inherent to the
  Omarchy plugin system, not specific to this plugin.
- **A user who allowlists the wrong thing.** Adding a hostile host to `config.toml`, or trusting
  a malicious board, is outside what any allowlist can prevent.
- **A compromised Raspberry Pi.** Output from `pinctrl` is parsed, not trusted for control flow,
  but a hostile Pi can lie about pin state.
- **`confirm=true` is model-controlled.** Destructive MCP annotations ask the *client*
  to prompt a human; the boolean itself is just a second tool argument. A steered
  model can retry with `confirm=true`. HMAC+confirm still stops a single unconsidered
  call. A real human gate requires a client that honours `destructive_hint`.
- **Physical attacks.** Swapping a board for a device with the same USB VID/PID and
  the same USB serial defeats identification. USB identifiers are claims, not proof.
  The upload token is bound to sketch path, FQBN, USB serial (when sysfs has one),
  build path, artifact digest, and expiry. Two boards that both lack a USB serial number can still be swapped.
- **`~/.ssh/config` Host aliases and DNS.** Command-line `-o` pins host-key checking
  and disables agent/X11 forwarding, `PermitLocalCommand`, port forwards, and
  `ProxyCommand`. Canonicalization, `ProxyJump`, and a `Host` alias that points at a
  different machine remain the user's SSH configuration. The configured name must
  already have a `known_hosts` entry.
- **Local processes with the same UID.** Private upload directories isolate builds
  from ordinary concurrent rebuilds and other users. They are not a sandbox
  against a hostile process already running as the same user.
- **Late serial replies.** Query input resets discard data already received by the
  reader or queued by the OS. Hardware protocols need request IDs to distinguish
  an old reply that only arrives after a new command has been sent.
- **Serial group membership outlives the plugin.** `uucp` (or `dialout`) grants access to
  *every* serial device on the machine, for every program the user runs, and survives
  uninstalling the plugin. It is the smallest grant that makes USB serial work without udev
  rules or a setuid helper, but it is not scoped to this plugin. `sudo gpasswd -d "$USER" uucp`
  gives it back.
- **The audit log is tamper-evident, not tamper-proof.** It is a file owned by the user, so a
  process running as that user can rewrite it. Chaining each record to the one before means
  `audit_status` reports the damage instead of the log quietly agreeing with whoever edited it
  last. Shipping records off the machine is the only way to do better, and is out of scope here.
  Each actuation writes two records: the intent before anything moves, then `<event>_done` or
  `<event>_failed` afterwards, each with the writing server's `pid`. Every Claude Code session
  runs its own server, so appends hold an exclusive `flock` on the log and read the chain head
  under it; concurrent sessions extend one chain rather than forking it.
- **Rate budgets are per server process.** Serial, GPIO and MING budgets live in the memory of
  one MCP server. Each Claude Code session starts its own, so with several sessions open the
  effective cap on a pin or topic is that many times the configured value. The audit log is
  shared and records every one of those operations; the budgets are a brake on a runaway loop
  in one session, not a machine-wide interlock.
- **MING services are trusted to be what they claim.** A compromised broker or Node-RED can
  send the model any payload, flow name or dashboard title. Results are marked as data and
  capped in size, but their content is chosen by whoever controls the service. An inject
  node or published topic does whatever the flow or subscriber behind it does; the
  allowlist names the trigger, not the consequence.
- **Loopback cleartext.** `tls = false` and `http://` are accepted without a waiver for
  127.0.0.1 and localhost. Another local user can connect to those ports too; the services'
  own authentication is what stops them.
- **Supply chain of dependencies.** `mcp` and `pyserial` are pinned with hashes and audited by
  `pip-audit` and Dependabot, but their upstream integrity is ultimately trusted.
- **QML is not statically linted in CI.** `qmllint` needs Qt plus Quickshell's type
  registrations, which are not available on a hosted runner. `BoardsModel.js` is syntax-checked;
  `Panel.qml` is reviewed by hand.
- **The final upload write and Pi GPIO are not verified against physical hardware.**
  Compiling is verified end to end (a real `.hex` is produced through the MCP server) and
  the upload gates are tested, but `upload_sketch` has never actually written to a board
  and GPIO has never run against a real Pi. Simulators cannot close this gap: they expose
  no local `/dev/ttyACM*`, and a `socat` pseudo-terminal is correctly refused by the
  post-`realpath` allowlist check.

The SSH client requires the configured host's key to already exist in the user's
`known_hosts`; first-use keys are not accepted automatically. There is no
`pi.host_keys` fingerprint field in `config.toml` — a field that is parsed but
not applied would look like pinning without being pinning. The ssh argv puts
`--` before the destination so OpenSSH cannot treat it as part of the remote
command, and the host allowlist is checked again immediately before
`subprocess.run`. Flashing also requires a writable audit log before starting
and rechecks that the connected recognised board's suggested FQBN matches the
requested FQBN. If a serial session was open on that port, a failed reopen after
upload is returned as `session_restored: false` rather than ignored.
`flash.allow` defaults to false and requires `sketch_roots` when enabled.

## Assurance

`get_hardware_reference` and the local reference CLI export family metadata and
selected policy values only. They do not enumerate boards, open serial ports,
contact remote hosts, or grant authorization. Hostnames, sketch paths, endpoint
credentials, and USB serials are excluded. The snapshot can become stale;
execution still passes through the existing MCP handlers. The project-owned
reference is preparation for an MHS adapter, not a verified MHS contract or a
description of electrical limits and physical interlocks.

- 361 automated tests, no hardware required, including adversarial path-escape cases
  (`../../dev/sda`, symlink redirection, unlisted hosts, out-of-range pins),
  upload-token forgery (wrong sketch, wrong board, tampered signature, extended expiry),
  and MING injection and transport cases (Flux and line-protocol injection, widened
  topic filters, redirects, proxies, oversized packets, wrong TLS names) against
  in-process MQTT and HTTP fakes.
- CI on every push: pytest across Python 3.11–3.13, `ruff` with the flake8-bandit ruleset,
  `shellcheck`, `pip-audit`, and `zizmor` auditing the workflows.
- CodeQL (`security-and-quality` queries) on every push to `main`, every pull request,
  and weekly.
- Required status checks on `main` are the test matrix (3.11-3.13), `shell scripts`,
  `plugin manifest` and `security audit`. `analyze python`, `native core`,
  `dotnet contracts` and `documented claims` run on every change but are **not yet
  required**; adding them is a branch-protection change made in the repository
  settings, and this file will say otherwise only once that is true. `tests (py3.14)`
  is deliberately excluded: it is `continue-on-error`.
- GitHub Actions pinned to full commit SHAs; workflow tokens default to `contents: read`.

### OpenSSF Scorecard: expected results

`.github/workflows/scorecard.yml` publishes a weekly Scorecard assessment to code
scanning. Some checks cannot pass for a single-maintainer project and are left
failing rather than worked around:

- **Code-Review** — needs pull requests approved by someone other than the
  author. There is one maintainer, and self-approval is not possible. Every change
  still lands through a pull request gated on the required CI checks.
- **Fuzzing** — no fuzzer integration (OSS-Fuzz, ClusterFuzzLite, Atheris). The
  untrusted-input parsers are covered by adversarial unit tests instead; see above.
- **CII-Best-Practices** — no OpenSSF Best Practices badge has been applied for.
- **Maintained** — fails for any repository younger than 90 days, and clears on
  its own after that.
- **SAST** — scores below 10 only because commits made before CodeQL was added
  are still inside Scorecard's window. Every commit since is analyzed.

The corresponding code-scanning alerts stay open with a pointer to this section,
rather than being dismissed: an open alert with a stated reason is honest about
the gap, where a dismissal hides it from anyone reading the security tab. A regression in any other Scorecard check (Pinned-Dependencies,
Token-Permissions, Branch-Protection, Dangerous-Workflow) is treated as a real
finding.
