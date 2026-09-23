# Connecting Claude to your MING stack

This tutorial connects the plugin to an MQTT, InfluxDB, Node-RED and Grafana
setup **you already run**, on this machine or elsewhere on your network. If you
don't have one yet, [`examples/ming-stack`](../examples/ming-stack) creates a
complete stack in Docker and writes the configuration for you. Its
[greenhouse demo](../examples/ming-stack/demo) shows the tools in use.
For a physical board and a results dashboard, follow the
[Arduino Uno → MING → Grafana tutorial](../examples/ming-stack/arduino).

It takes about fifteen minutes. You don't need all four services: set up only
the ones you have.

## What Claude can do once it's connected

| You ask | Tool | Writes? |
| --- | --- | --- |
| "Is the stack up?" | `ming_status` | no |
| "What's on `sensors/greenhouse/#` right now?" | `mqtt_subscribe` | no |
| "What measurements are in the `sensors` bucket?" | `influx_measurements` | no |
| "Average temperature per 5 minutes over the last day" | `influx_query` | no |
| "Which inject buttons does Node-RED have?" | `nodered_flows` | no |
| "Find the greenhouse dashboard" | `grafana_dashboards` | no |
| "Set the fan to on" | `mqtt_publish` | yes: confirmation, rate limit, audit |
| "Log this setpoint to the `claude` bucket" | `influx_write` | yes: confirmation, rate limit, audit |
| "Press the *Fan on* button" | `nodered_inject` | yes: confirmation, rate limit, audit |
| "Mark this change on the dashboard" | `grafana_annotate` | yes: confirmation, rate limit, audit |

Everything outside what `config.toml` allows is refused before any connection is
made. That includes topics, buckets, inject nodes and Grafana annotations you
haven't enabled.

## 1. Give Claude its own least-privilege credentials

Don't hand Claude an admin account. Each service can limit its own credential,
and that limit still holds if the plugin's allowlist is ever wrong.

**Mosquitto:** create a user and restrict it in the ACL file:

```bash
mosquitto_passwd /etc/mosquitto/passwd claude
```
```
# /etc/mosquitto/acl
user claude
topic read sensors/#
topic readwrite actuators/#
```

**InfluxDB 2:** create a token that can read your data buckets and write only
to a bucket of its own:

```bash
influx bucket create --name claude --retention 0
influx auth create --description "omarchy-hardware" \
  --read-bucket <sensors-bucket-id> --read-bucket <claude-bucket-id> \
  --write-bucket <claude-bucket-id>
```

**Node-RED:** in `settings.js`, map a static API token to a user that can only
read flows and press inject buttons. It can't deploy. Read the token from a file
you mount into the container (an environment variable shows in `docker inspect`),
and compare it in constant time:

```js
const crypto = require("crypto");
const fs = require("fs");
const CLAUDE_TOKEN = fs.readFileSync("/run/secrets/nodered-token", "utf8").trim();
const same = (given, expected) => {
  const a = Buffer.from(String(given)), b = Buffer.from(expected);
  return b.length > 0 && a.length === b.length && crypto.timingSafeEqual(a, b);
};

adminAuth: {
  type: "credentials",
  users: [/* your admin user */],
  tokens: async (token) =>
    same(token, CLAUDE_TOKEN) ? { username: "claude", permissions: ["read", "inject.write"] } : null,
},
```

[`examples/ming-stack/nodered/settings.js`](../examples/ming-stack/nodered/settings.js) is a
complete, working version.

**Grafana:** Administration → Service accounts → add `claude` with role
**Viewer**, then create a token. Use **Editor** only if Claude should add
annotations. An Editor can also change dashboards that are not provisioned.

Save each secret in its own file, readable only by you. The plugin refuses a
credential file that other users can read, or one that is a symlink:

```bash
install -d -m 700 ~/.config/omarchy-hardware/ming
printf '%s' 'the-password' > ~/.config/omarchy-hardware/ming/mqtt-password
chmod 600 ~/.config/omarchy-hardware/ming/*
```

Instead of `*_file`, you can use `*_env` (`password_env`, `token_env`). That names
an environment variable, which must be exported in the environment Claude Code
starts from. A file is usually simpler.

## 2. Decide on transport security

The rules are strict because a token or password sent in cleartext is readable
by anyone on the network path:

- **Remote services** need `https://` URLs and MQTT over TLS (`tls` is on by
  default, port 8883).
- **Services on this machine** (`127.0.0.1`, `localhost`) may use `http://` and
  plain MQTT without any waiver.
- **A private CA:** point `ca_file` at its certificate. The system trust store is
  not needed.
- **Accepting cleartext to a remote host** requires writing
  `allow_insecure = true` in that target's `security` table. That makes the
  choice explicit and visible in the config.

## 3. Write the config

Append this to `~/.config/omarchy-hardware/config.toml`. The file must stay mode
600. Each `name` (lower-case letters, digits, `-` and `_`) is how you and Claude
refer to a service; URLs and hostnames are never shown to the model.

```toml
[ming]
allow = true                # nothing works until this is true
# timeout = 10              # seconds per request, 1-30
# max_payload_bytes = 4096  # largest publish or point, up to 65536
# write_budget_per_min = 60 # writes per target per minute

[[ming.mqtt]]
name = "plant"
host = "broker.lan"
# port = 8883               # default follows tls
subscribe = ["sensors/#"]   # filters; Claude may narrow them, never widen them
publish = []                # exact topics; start empty
security = { ca_file = "~/.config/omarchy-hardware/ming/ca.pem", username = "claude", password_file = "~/.config/omarchy-hardware/ming/mqtt-password" }

[[ming.influxdb]]
name = "plant"
url = "https://influx.lan:8086"
org = "home"
read_buckets = ["sensors", "claude"]
write_buckets = ["claude"]
security = { ca_file = "~/.config/omarchy-hardware/ming/ca.pem", token_file = "~/.config/omarchy-hardware/ming/influxdb-token" }

[[ming.nodered]]
name = "plant"
url = "https://nodered.lan:1880"
inject_nodes = []           # fill in after step 5
security = { ca_file = "~/.config/omarchy-hardware/ming/ca.pem", token_file = "~/.config/omarchy-hardware/ming/nodered-token" }

[[ming.grafana]]
name = "plant"
url = "https://grafana.lan:3000"
annotate = false
security = { ca_file = "~/.config/omarchy-hardware/ming/ca.pem", token_file = "~/.config/omarchy-hardware/ming/grafana-token" }
```

Start read-only (`publish = []`, `inject_nodes = []`, `annotate = false`) and
add write permissions once reading works. If you have several brokers or
databases, add more `[[ming.mqtt]]` entries with different names and tell Claude
which one to use ("on the `lab` broker ...").

## 4. Load it and check

The MCP server reads the config on every call, but tools that were not loaded
before need a fresh session. In Claude Code, run `/mcp` and reconnect
`omarchy-hardware` (or start a new session). Then ask:

> *Check the MING stack.*

`ming_status` probes every target in parallel. It reports which are reachable
and what each one allows. If something fails:

| Error | Usually means |
| --- | --- |
| `CONFIG_ERROR` ... readable by other users | `chmod 600` the credential file |
| `CONFIG_ERROR` ... environment variable not set | export it before starting Claude Code, or use `*_file` |
| `CONFIG_ERROR` ... cleartext | `http://` or `tls = false` to a host that isn't this machine; use TLS, or set `allow_insecure` on that target |
| `SERVICE_UNREACHABLE` ... could not reach / connect | wrong host or port, a firewall, or the service is down |
| `SERVICE_ERROR` ... HTTP 401/403, or MQTT "not authorized" | the credential is wrong or lacks that permission on the service |
| ... TLS failed / TLS handshake failed | `ca_file` is missing, isn't the CA that signed the server, or the name in the URL/host isn't on the server's certificate |
| `HOST_NOT_ALLOWED` ... disabled | `[ming] allow` is still `false` |

## 5. Use it

Read first:

> *What's the latest greenhouse temperature, and how fast is it rising?*

Claude runs `mqtt_subscribe` on a filter inside your `subscribe` list for the live
value, then `influx_query` with `aggregate = "mean"` and `every = "1m"` for the
trend. Raw Flux is never accepted: queries are built from parameters.

> *Which inject buttons are there?*

`nodered_flows` lists tabs, node counts and inject nodes with their ids. Copy the
ids you want Claude to be able to press into `inject_nodes`.

Then enable only the writes you need:

```toml
[[ming.mqtt]]
publish = ["actuators/fan"]          # exact topic, no wildcards, no $SYS
[[ming.nodered]]
inject_nodes = ["fan0on", "fan0off"]
[[ming.grafana]]
annotate = true                      # needs an Editor service account
```

> *Turn the fan on with the Node-RED button and mark it on the greenhouse dashboard.*

Before each write, Claude Code asks you to confirm: the tools require
`confirm=true`, and destructive tools are flagged for your client to prompt.
Each write is also recorded in the audit log before it's sent, then again with
its outcome.

> *Show me the audit log.*

`audit_status` checks the hash chain in
`~/.local/state/omarchy-hardware/audit.log`. Each record has a digest of the
payload rather than the payload itself.

## What it will not do

- **Deploy or edit Node-RED flows.** A flow can run arbitrary code on the Node-RED
  host, so there is no tool for it. Your token shouldn't allow it either.
- **Run raw Flux,** or write outside `write_buckets`.
- **Publish to a wildcard or `$` topic,** or subscribe wider than `subscribe`.
- **Show URLs, hostnames or credentials to the model.** Targets appear by name only.
- **Follow redirects or proxies.** Responses are size-capped, and payloads from the
  services are treated as data, not instructions.

The allowlist names the *trigger*, not the *consequence*: publishing to
`actuators/fan` does whatever your devices and flows do with it. Only allowlist
actions you'd be comfortable seeing happen with one confirmation.
