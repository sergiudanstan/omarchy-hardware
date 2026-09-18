# Greenhouse demo

A five-minute demonstration of Claude operating a MING stack from Omarchy: a
simulated greenhouse overheats, Omarchy raises a notification, and Claude reads
the sensors, switches the fan on, and marks the moment on a live Grafana
dashboard, all through the omarchy-hardware plugin's allowlisted tools.

```
greenhouse device ──MQTT/TLS──► Mosquitto ──► Node-RED bridge ──► InfluxDB ──► Grafana dashboard
        ▲                           │                                              ▲
        └──── actuators/fan ◄───────┴──── Claude (mqtt_publish, nodered_inject) ───┘ grafana_annotate
                                    └──► demo/notify.py ──► Omarchy notifications
```

| Piece | What it does |
| --- | --- |
| `greenhouse.sh` | The "device": warms about 2.4 °C a minute in the sun, cools while the fan runs, publishes `sensors/greenhouse/{temperature,humidity,fan}` as MQTT user `device`, obeys `actuators/fan`. Compose profile `demo`. |
| `flow.json` | Node-RED "Greenhouse" flow: bridges the readings into InfluxDB (its own write-only token) and has **Fan on** / **Fan off** inject buttons (`fan0on`, `fan0off`). |
| `../grafana/provisioning/dashboards/greenhouse.json` | The "Greenhouse" dashboard: temperature with the 30 °C line, current value, fan state, humidity, and Claude's annotations. Provisioned, so read-only. |
| `notify.py` | Omarchy notifications when the greenhouse passes 30 °C and when the fan switches. It uses the plugin's own config, credentials and MQTT client. |
| `setup.sh` | Adds all of the above to a stack made by `../bootstrap.sh`. |
| `omarchy-demo.sh` | Opens the dashboard as an Omarchy web app and starts the notifications. |

## Set up (once)

```bash
cd examples/ming-stack
MING_CLIENT_DIR=~/.config/omarchy-hardware/ming ./bootstrap.sh   # if not done yet
./demo/setup.sh --annotate
```

`--annotate` makes Claude's Grafana service account an Editor so it can add
annotations. Grafana refuses to overwrite or delete the Greenhouse dashboard
because it is provisioned, but an Editor can still create or change other,
unprovisioned dashboards. Leave `--annotate` out if that matters.

In `~/.config/omarchy-hardware/config.toml`, the `[ming]` block from
`client/ming.toml` needs three edits, which `setup.sh` prints:

```toml
[[ming.mqtt]]
publish = ["actuators/fan"]
[[ming.nodered]]
inject_nodes = ["fan0on", "fan0off"]
[[ming.grafana]]
annotate = true
```

Start a new Claude Code session afterwards so the MING tools are loaded.

## Run it

```bash
./demo/omarchy-demo.sh --trust-ca
```

This opens the dashboard as a web app (log in as `admin` with the password in
`secrets/grafana-admin-password`) and starts the notifications. `--trust-ca`
adds the stack's CA to the browser so there is no certificate warning. That is
safe because the CA is **name-constrained**: it can only vouch for localhost,
127.x, the service names and the hostnames given to `bootstrap.sh`, never for
any other site. Leave it out to click through the warning instead.

Within a couple of minutes the greenhouse passes 30 °C and Omarchy shows
**Greenhouse too hot**. Then, in Claude Code:

1. *"Check the MING stack."* → `ming_status`: four services, their allowlists.
2. *"How warm is the greenhouse, and how fast is it rising?"* →
   `mqtt_subscribe` for the live value, `influx_query` with a 1-minute mean for
   the trend.
3. *"Switch the fan on with the Node-RED button, and mark it on the
   Greenhouse dashboard."* → `nodered_inject fan0on` and `grafana_annotate`,
   each asking for your confirmation first. Omarchy shows **Greenhouse fan ON**,
   the Fan panel turns blue, an orange annotation appears, and the curve bends
   down.
4. *"Once it's below 25 °C, turn the fan off over MQTT."* → `influx_query` /
   `mqtt_subscribe`, then `mqtt_publish actuators/fan off`.
5. *"Show me the audit log."* → `audit_status`: every write, chained.

Things worth trying, to show the limits hold:

- *"Turn the heater on"* → `actuators/heater` is not in the publish allowlist.
- *"Subscribe to everything"* → `#` is wider than the configured filters.
- *"Write a test point into the sensors bucket"* → it is read-only for Claude.
- *"Deploy a Node-RED flow that ..."* → there is no tool for it, and Claude's
  Node-RED token could not do it anyway.

## Reset

```bash
docker compose --profile demo restart greenhouse   # back to 27 °C; the fan keeps its last
                                                   # command, which the broker retains
pkill -f demo/notify.py                            # stop notifications
docker compose --profile demo stop greenhouse      # stop the device
certutil -d sql:$HOME/.pki/nssdb -D -n "MING example CA"   # untrust the CA
```
