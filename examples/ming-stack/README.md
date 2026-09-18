# MING stack example

Mosquitto, InfluxDB 2, Node-RED and Grafana, set up the way the plugin's MING
tools expect: TLS everywhere, no anonymous access, and credentials for Claude
that can do only what the plugin allows.

| Service | Port | Claude's credential | What it can do |
| --- | --- | --- | --- |
| Mosquitto 2.0.22 | 8883 (TLS) | user `claude` | read `sensors/#`, read and write `actuators/#` (ACL) |
| InfluxDB 2.9.1 | 8086 (HTTPS) | scoped token | read `sensors` and `claude`, write `claude` |
| Node-RED 5.0.7 | 1880 (HTTPS) | static API token | read flows, press inject buttons; **cannot deploy** |
| Grafana 13.2.2 | 3000 (HTTPS) | service account | Viewer; Editor with `MING_GRAFANA_ANNOTATE=1` |

The plugin narrows this further: `config.toml` names the exact topics, buckets
and inject nodes, and every write needs `confirm=true`.

## Run it

Needs Docker Engine with the compose plugin, `openssl`, `curl`, `python3` and
`setfacl` (package `acl`), and a user in the `docker` group.

```bash
# On the Omarchy machine itself: everything on 127.0.0.1.
MING_CLIENT_DIR=~/.config/omarchy-hardware/ming ./bootstrap.sh

# On a Pi, reachable from the Omarchy machine as pi.local:
MING_BIND=0.0.0.0 MING_HOSTNAMES="pi.local 192.168.1.20" ./bootstrap.sh
```

`bootstrap.sh` makes a private CA and a server certificate, random passwords and
tokens under `secrets/` (mode 700), hashes the Mosquitto passwords, starts the
services, creates the scoped InfluxDB tokens and the Grafana service account,
and writes `client/`: the CA certificate, Claude's four credentials, and
`ming.toml`, the block to append to `~/.config/omarchy-hardware/config.toml`.
Re-running it keeps everything that already exists.

For a remote stack, copy `client/` to `~/.config/omarchy-hardware/ming` on the
Omarchy machine (directory mode 700, files 600), append `client/ming.toml` to
`config.toml`, then ask Claude to run `ming_status`.

Devices publish as user `device` (password in `secrets/mqtt-device-password`)
to `sensors/...` over TLS with `certs/ca.crt`. Add more users with
`mosquitto_passwd` and the ACL in `mosquitto/acl`.

## Security notes

- The containers read their keys through file ACLs (`setfacl`) granted to the
  image users (Mosquitto 1883, InfluxDB and Node-RED 1000, Grafana 472), so
  nothing under `secrets/` or `certs/server.key` is world-readable. Rootless
  Docker or user-namespace remapping changes those IDs; adjust `bootstrap.sh`.
- The CA key stays in `certs/ca.key`. Anyone holding it can impersonate these
  services to the plugin; keep the directory private, or delete the key once
  the certificates are issued (re-issuing then needs a new CA).
- Ports bind to 127.0.0.1 unless `MING_BIND` says otherwise. Exposing the stack
  beyond a trusted LAN is not what this example is for.
- The InfluxDB admin token in `secrets/influxdb-admin-token` is full access.
  Claude never gets it.

## Status

Brought up end to end on 2026-09-18 (x86_64, Docker 29.7.2), with all ten
plugin tools driven over MCP stdio against it. Checked on the service side as
well: with Claude's own credentials, Node-RED refuses a flow deploy (401),
InfluxDB refuses a write to `sensors` (403), Grafana refuses an annotation from
the Viewer account (403), and Mosquitto drops a publish outside the ACL.
Re-running `bootstrap.sh` keeps every credential. Not yet run on a Pi.

Known noise: Mosquitto 2.0.22 warns that `passwd` and `acl` are not owned by its
own user and that future versions will refuse them. Bind mounts cannot change
ownership without root, so an image upgrade past 2.0.x needs a different way of
providing those two files. Grafana logs errors for the provisioning folders
this example does not use (plugins, alerting, dashboards); they are harmless.
