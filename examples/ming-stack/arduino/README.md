# Arduino Uno → MING → Grafana, step by step

Use a real Arduino Uno to check **Mosquitto, InfluxDB, Node-RED and Grafana**
and view the results in a dashboard. The Uno echoes three diagnostic numbers
over USB; a short-lived host adapter publishes exactly those returned values.
These are **not temperature, humidity or other sensor readings**.

```text
Uno echo sketch → USB → validate.py → MQTT/TLS → Node-RED → InfluxDB → Grafana
```

This example uses the local [MING stack](..) and its existing
[greenhouse flow](../demo/flow.json). It does not exercise `serial_bridge_start`:
that continuous bridge expects JSON-object lines, while this sketch prints
`ECHO:<value>`. No sensors, LEDs, relays or GPIO wiring are needed.

## 1. Check prerequisites

- An installed omarchy-hardware plugin and its Python environment.
- One USB-connected Arduino Uno identified as `arduino:avr:uno`, available to
  your user and not held by a serial monitor.
- Docker Engine with Compose, and the prerequisites listed in the
  [stack README](../README.md#run-it).
- `arduino-cli` and the `arduino:avr` core if you need to load the sketch.

Run the commands below from this repository's root. Keep a terminal there:

```bash
git clone https://github.com/sergiudanstan/omarchy-hardware.git
cd omarchy-hardware
```

Skip the clone if you already have this checkout. The commands assume the
plugin's default Python environment at
`~/.local/share/omarchy-hardware/venv/bin/python`.

## 2. Start the stack and install its example flow

For a **new example stack**:

```bash
cd examples/ming-stack
MING_CLIENT_DIR=~/.config/omarchy-hardware/ming ./bootstrap.sh
./demo/setup.sh
docker compose --profile demo stop greenhouse
cd ../..
```

`demo/setup.sh` creates or updates the greenhouse Node-RED flow and starts the
simulator. The next command stops that simulator so you can focus on the Uno.
The four core services stay running. Leave out `--annotate`: validating and
querying the dashboard only require the existing Viewer service account.

For an **already configured stack with this flow**, skip setup and start only
the four services:

```bash
docker compose -f examples/ming-stack/compose.yaml up -d mosquitto influxdb nodered grafana
```

Do not rerun the demo setup over a customized greenhouse flow: it updates that
flow. This runner requires its existing `sensors/greenhouse/+` → `greenhouse`
measurement route and the `sensors` bucket. The default example uses org `home`.
It is not a generic test for arbitrary Node-RED flows.

## 3. Connect the plugin with read access

Merge the generated `examples/ming-stack/client/ming.toml` into
`~/.config/omarchy-hardware/config.toml`. Append it only if you do not already
have a `[ming]` section; TOML does not permit duplicate tables. Keep that file
mode 600 and reconnect the plugin in your MCP client.

The generated configuration names all four services `stack`, enables MING,
subscribes to `sensors/#`, and permits reading `sensors` in InfluxDB. Keep
`publish = []`, `inject_nodes = []` and `annotate = false` for this tutorial;
existing narrower or broader settings need not be changed. Do not add telemetry
publish rights to the MCP identity.

Ask your MCP client:

> Check `ming_status` and `list_boards`. Confirm the four `stack` targets are
> reachable and exactly one Arduino Uno is writable and idle.

The separate host adapter uses Mosquitto's existing **device** account, whose
ACL permits `sensors/#`. Its password stays in
`examples/ming-stack/secrets/mqtt-device-password`. The runner requires that
file to be private and refuses symlinks. It does not use the plugin's
`mqtt_publish` permission or write budget: its separate `--confirm-publish`
flag authorizes exactly three fixed, non-retained telemetry messages and its
writes are audited. It never sends an actuator command.

## 4. Prepare the Uno echo sketch once

If the Uno already runs the repository's `hw-validation` sketch at 115200 baud,
skip this step. **Loading it replaces the current sketch.** Keep your existing
source and a way to restore it before proceeding.

```bash
mkdir -p ~/Arduino
cp -R mcp/hardware_validation/hw-validation ~/Arduino/
```

In the plugin's existing `[flash]` table, enable `allow = true` and include
`~/Arduino/hw-validation` in `sketch_roots`. Preserve any other roots you need.
Then ask your MCP client:

> Compile `~/Arduino/hw-validation` for `arduino:avr:uno`, using the Uno port
> returned by `list_boards`. Upload that exact result with its compile token
> and `confirm=true`. I authorize replacing this Uno's sketch with the
> serial-only validation sketch.

This uses the normal compile-token and board-binding gates. Reuse the returned
token, not an example token. The sketch prints `HWVAL READY`, responds to
`PING` with `PONG`, and echoes other lines with `ECHO:`. It does not drive GPIO.
Close any serial monitor before the next step. You can turn `[flash] allow`
back off: the MING runner never uploads firmware. For the separate 26-check
flash regression suite, see [hardware validation](../../../mcp/hardware_validation/README.md).

## 5. Run the physical MING check

```bash
~/.local/share/omarchy-hardware/venv/bin/python \
  examples/ming-stack/arduino/validate.py \
  --launcher ./bin/hardware-mcp \
  --device-password ./examples/ming-stack/secrets/mqtt-device-password \
  --output ./examples/ming-stack/arduino/results/first-run \
  --confirm-publish
```

Choose a new output directory for every run. Existing directories are refused,
so earlier evidence is preserved. Use `--target NAME` if all four configured
services share a name other than `stack`, or `--datasource-uid UID` if Grafana's
existing InfluxDB datasource differs from `ming-influxdb`.

The runner opens the Uno at **115200** (which normally resets it), verifies the
banner, sends `314.159`, `271.828` and `161.803`, and closes the serial session.
It publishes the actual echoed values at two-second intervals to an isolated
`sensors/greenhouse/uno_validation_<run>` topic. It observes MQTT through MCP,
checks Node-RED's writes through `influx_query`, then queries Grafana's existing
datasource. It also checks that plugin configuration is unchanged and the
audit chain is intact.

Success means **12/12 checks**, including the same three values in MQTT,
InfluxDB and Grafana. Failure exits nonzero and records the partial result;
later checks are not claimed as passed. The script writes:

- `dashboard.json` — importable Grafana results, with a query for this run.
- `results.json` — supporting check evidence, without USB serials or tokens.

Only three diagnostic telemetry messages are intentionally published. Each
run adds an InfluxDB field; this is a bounded manual check, not a scheduled
load generator or a long-running device bridge.

## 6. Open the results in Grafana

Open [local Grafana](https://127.0.0.1:3000), sign in using your existing
dashboard-editing account, and choose **Dashboards → New → Import**. Upload
`results/first-run/dashboard.json`, then import and open it. On the example
stack the admin password is in `secrets/grafana-admin-password`; do not copy
credentials into a dashboard or Git. For a remote stack, use its configured
hostname instead of `127.0.0.1`.

The browser must trust the example CA; the Python runner already verifies TLS
with the configured CA file. If the browser shows a certificate warning,
review your local CA setup before continuing. Do not disable TLS in the runner.

The dashboard uses a **fixed time range around the run**, so returning later
still shows those three points. The chart reads InfluxDB, while the pass/fail
panels record the saved check evidence. They are not live service-health probes.
Changing the chart's time range does not rerun the checks. Importing the file
does not insert historical points into another database.

The existing Greenhouse dashboard shows temperature, humidity and fan fields;
it does **not** show this isolated diagnostic field. Use the new validation
dashboard for results. Importing requires a dashboard-writing account, but
there is no need to upgrade the plugin's Viewer token. Share the dashboard
link as the primary result and keep `results.json` alongside it as evidence.

## 7. Interpret failures and the recorded runs

| Symptom | What to check |
|---|---|
| Service unreachable | Start the four containers; verify the configured TLS endpoints and CA. |
| Uno unavailable or busy | Reconnect USB, close monitors, and check the actual user's serial-device permissions. |
| No `HWVAL READY` or wrong echo | Confirm the echo sketch and 115200 baud; the runner will not reflash for you. |
| MQTT passes, InfluxDB fails | Check the Node-RED MQTT connection, route and write credential; inspect its logs. |
| Fewer points than messages | The example flow uses `precision=s`; same-second values can replace one another. Keep the two-second spacing. |
| InfluxDB passes, Grafana fails | Check datasource UID, its read token and TLS configuration. |
| Chart empty after import | Use the saved run time range and the same stack; check retention and datasource UID. |
| Output directory exists | Choose a new directory rather than deleting old evidence. |

On **2026-09-21**, an Arduino Uno (`2341:0043`) running the echo sketch was
tested against plugin commit `7e445e5` and this local stack:

| Run | Result | Evidence |
|---|---|---|
| Rapid, ~0.3 s between messages | Uno and MQTT passed; InfluxDB kept 2/3 points; Grafana check was not reached | [Initial run](evidence/rapid-2026-09-21.json) |
| Paced, 2 s between messages | 12/12 checks passed; all three values returned through Grafana | [Passing run](evidence/paced-2026-09-21.json) |
| Reusable tutorial runner, 2 s between messages | 12/12 checks passed on the same physical Uno | [Runner evidence](evidence/tutorial-2026-09-21.json) · [Importable dashboard](evidence/tutorial-dashboard-2026-09-21.json) |

The recorded timestamps and flow configuration indicate same-second point
replacement in the rapid run. Spacing the messages is a workaround, not a
flow fix. Applications needing higher sample rates should design and validate
their timestamp precision separately. The validation does not establish
real-sensor accuracy, physical actuator behavior, continuous JSON bridging,
Pi-hosted operation or browser rendering. Firmware, flow and plugin policy
were unchanged during these runs. The reusable runner's dashboard is included
as a recorded example; its chart requires the originating InfluxDB points.

## 8. Finish

The runner closes its serial session. It leaves the stack and recorded points
available so the results dashboard continues to work. The standalone echo
sketch stays on the Uno. Stop the example stack only when finished reviewing:

```bash
docker compose -f examples/ming-stack/compose.yaml stop
```

That preserves container volumes; do not remove volumes to clean up a test.
Restore your own sketch through the normal confirmed upload process if needed.
