---
description: Stream the board's readings into the MING stack (MQTT, InfluxDB, Grafana)
---

Get the board's readings into the user's MING stack.

1. Call `ming_status`. If MING is off or no broker is configured, explain that
   `[ming]` in config.toml and `docs/ming-tutorial.md` set it up, and stop there.
2. The firmware must print one JSON object per reading, such as
   `{"t":21.4,"h":48}`. If it does not, change the sketch first with `/hw-build`.
3. Pick the topic from the broker's publish allowlist. If none fits, tell the user
   which exact topic to add under that broker's `publish` list. Do not edit the
   config yourself.
4. Explain what the bridge does:
   - it publishes each reading, at most one every `min_interval_ms`;
   - it runs for `duration_s`, at most an hour;
   - whatever subscribes to the topic may act on the readings.

   Once the user agrees, call `serial_open` if needed, then
   `serial_bridge_start(session_id, topic, confirm=true)`.
5. Check the flow after about 30 seconds:
   - `serial_bridge_status` should show messages published and no errors;
   - `mqtt_subscribe` on the topic should show the readings, if it is allowlisted
     for subscribe;
   - `influx_measurements` and `influx_query` should show them stored, if the
     stack writes that topic to InfluxDB.
6. Call `grafana_dashboards` to find a dashboard for the data. Offer a
   `grafana_annotate` note that marks when this firmware went live.

$ARGUMENTS
