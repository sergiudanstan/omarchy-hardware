"""Physical Uno/MING check; writes three diagnostic MQTT messages, never flashes."""

import argparse
import asyncio
import json
import math
import time
from datetime import UTC, datetime
from pathlib import Path

from mcp.client.stdio import stdio_client
from omarchy_hardware import audit, config, http_lite, ming, mqtt_lite
from results_dashboard import build_dashboard

from mcp import ClientSession, StdioServerParameters

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--launcher", type=Path, required=True, help="Path to bin/hardware-mcp")
parser.add_argument(
    "--device-password",
    type=Path,
    required=True,
    help="Mode-600 device MQTT password file",
)
parser.add_argument(
    "--target",
    default="stack",
    help="Configured name for each of the four MING services",
)
parser.add_argument(
    "--datasource-uid",
    default="ming-influxdb",
    help="Existing Grafana InfluxDB datasource UID",
)
parser.add_argument(
    "--output",
    type=Path,
    required=True,
    help="New directory for results.json and dashboard.json",
)
parser.add_argument(
    "--confirm-publish",
    action="store_true",
    help="Allow three diagnostic telemetry writes",
)
args = parser.parse_args()
if not args.confirm_publish:
    parser.error("--confirm-publish is required; this writes three diagnostic MQTT messages")
ROOT = args.output.expanduser().resolve()
ROOT.mkdir(parents=True, mode=0o700, exist_ok=False)
RUN = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
FIELD = "uno_validation_" + RUN.lower()
TOPIC = "sensors/greenhouse/" + FIELD
VALUES = ["314.159", "271.828", "161.803"]
REPORT = {
    "run_id": RUN,
    "data_kind": "diagnostic numbers echoed by the physical Uno, not sensor measurements",
    "topic": TOPIC,
    "field": FIELD,
    "started_at": datetime.now(UTC).isoformat(),
    "publish_interval_seconds": 2,
    "note": "Existing flow uses second precision; rapid samples can overwrite one another.",
    "checks": [],
}


def check(name, passed, detail):
    REPORT["checks"].append({"check": name, "passed": bool(passed), "detail": detail})
    print(("PASS " if passed else "FAIL ") + name, flush=True)
    if not passed:
        raise RuntimeError(name)


async def main():
    c = config.load()
    broker = next(b for b in c.ming_mqtt if b.name == args.target)
    grafana = next(g for g in c.ming_grafana if g.name == args.target)
    if not c.ming_allow or not broker.security.tls or not grafana.url.startswith("https://"):
        raise RuntimeError("This example requires MING enabled, MQTT TLS and Grafana HTTPS")
    config_bytes = config.CONFIG_PATH.read_bytes()
    params = StdioServerParameters(command=str(args.launcher.expanduser().resolve()))
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as client,
    ):
        await client.initialize()

        async def call(tool, **args):
            result = await client.call_tool(tool, args)
            payload = json.loads(next(x.text for x in result.content if x.type == "text"))
            if not payload.get("ok"):
                raise RuntimeError(tool + ": " + json.dumps(payload.get("error")))
            return payload

        status = await call("ming_status")
        check(
            "All four MING services reachable",
            all(
                any(s["name"] == args.target and s["reachable"] for s in status[k])
                for k in ("mqtt", "influxdb", "nodered", "grafana")
            ),
            status,
        )
        found = await call("list_boards")
        unos = [b for b in found["boards"] if b.get("suggested_fqbn") == "arduino:avr:uno"]
        check(
            "Exactly one available Arduino Uno",
            len(unos) == 1 and unos[0]["writable"] and not unos[0]["busy"],
            {"uno_count": len(unos), "ports": [b["port"] for b in unos]},
        )
        opened = await call("serial_open", port=unos[0]["port"], baud=115200)
        sid = opened["session_id"]
        echoes = []
        try:
            banner = await call("serial_read", session_id=sid, max_wait_ms=5000, until="HWVAL READY")
            check(
                "Existing Uno validation sketch starts",
                "HWVAL READY" in banner["data"],
                {"banner": banner["data"]},
            )
            for value in VALUES:
                received = await call(
                    "serial_query",
                    session_id=sid,
                    data=value,
                    wait_ms=2000,
                    until="\n",
                    confirm=True,
                )
                line = received["data"].strip()
                check("Uno echo " + value, line == "ECHO:" + value, {"received": line})
                echoes.append(line.removeprefix("ECHO:"))
        finally:
            await call("serial_close", session_id=sid)
        remaining = await call("list_sessions")
        check(
            "Serial session released",
            not remaining["sessions"],
            {"open_sessions": len(remaining["sessions"])},
        )

        # This host adapter uses the stack's existing device identity, whose
        # Mosquitto ACL permits telemetry. The MCP client's actuator-only
        # publish allowlist is left intact; MCP observes using its read scope.
        password = ming.read_secret(None, str(args.device_password.expanduser()), "validation device")
        options = mqtt_lite.Options(
            host=broker.host,
            port=broker.port,
            tls=True,
            timeout=10,
            ca_file=broker.security.ca_file,
            username="device",
            password=password,
        )

        def publish():
            with mqtt_lite.Client(options, max_packet=8192) as device:
                for value in echoes:
                    if not math.isfinite(float(value)) or value not in VALUES:
                        raise RuntimeError("Unexpected diagnostic value; refusing publish")
                    audit.require(
                        "ming_validation_publish",
                        "publish Arduino diagnostic telemetry",
                        topic=TOPIC,
                        source="uno-serial-echo",
                        retain=False,
                    )
                    device.publish(TOPIC, value.encode(), qos=1, retain=False)
                    audit.note("ming_validation_publish_done", topic=TOPIC)
                    time.sleep(2)

        observer = asyncio.create_task(
            call(
                "mqtt_subscribe",
                broker=args.target,
                topic_filter=TOPIC,
                seconds=12,
                max_messages=3,
            )
        )
        await asyncio.sleep(2)
        await asyncio.to_thread(publish)
        messages = await observer
        received_values = [m["payload"] for m in messages["messages"] if m["topic"] == TOPIC]
        check(
            "MQTT/TLS receives all Uno diagnostic values",
            received_values == echoes,
            {
                "topic": TOPIC,
                "values": received_values,
                "retain": False,
                "publisher": "device",
            },
        )

        points = []
        for _ in range(10):
            queried = await call(
                "influx_query",
                influxdb=args.target,
                bucket="sensors",
                measurement="greenhouse",
                field=FIELD,
                start="-10m",
                limit=20,
            )
            points = queried["points"]
            if len(points) >= 3:
                break
            await asyncio.sleep(1)
        check(
            "Existing Node-RED flow stores all three values in InfluxDB",
            sorted(p["value"] for p in points) == sorted(float(x) for x in echoes),
            {"points": points},
        )

        headers = {
            "Content-Type": "application/json",
            **ming._bearer(grafana.security, "Grafana validation"),
        }
        flux = ming.build_query("sensors", "greenhouse", field=FIELD, start="-10m", limit=20)
        body = {
            "from": str(int((time.time() - 600) * 1000)),
            "to": str(int(time.time() * 1000)),
            "queries": [
                {
                    "refId": "A",
                    "datasource": {"type": "influxdb", "uid": args.datasource_uid},
                    "query": flux,
                    "maxDataPoints": 100,
                    "intervalMs": 1000,
                }
            ],
        }
        data = await asyncio.to_thread(
            lambda: http_lite.request(
                "POST",
                grafana.url + "/api/ds/query",
                headers=headers,
                body=json.dumps(body).encode(),
                timeout=15,
                ca_file=grafana.security.ca_file,
            ).json()
        )
        result = data.get("results", {}).get("A", {})
        values = []
        for frame in result.get("frames", []):
            for i, field in enumerate(frame.get("schema", {}).get("fields", [])):
                if field.get("type") == "number":
                    values.extend(v for v in frame.get("data", {}).get("values", [])[i] if v is not None)
        check(
            "Grafana datasource returns the same three Uno values",
            not result.get("error") and sorted(values) == sorted(float(x) for x in echoes),
            {
                "datasource_uid": args.datasource_uid,
                "status": result.get("status"),
                "values": values,
                "error": result.get("error"),
            },
        )
        check(
            "Plugin configuration unchanged",
            config.CONFIG_PATH.read_bytes() == config_bytes,
            {},
        )
        integrity = audit.verify()
        check(
            "Audit chain intact",
            integrity.get("ok"),
            {"ok": integrity.get("ok"), "records": integrity.get("records")},
        )


try:
    asyncio.run(asyncio.wait_for(main(), timeout=120))
    REPORT["passed"] = True
except Exception as error:
    REPORT["error"] = str(error)
    REPORT["passed"] = False
    raise
finally:
    REPORT["finished_at"] = datetime.now(UTC).isoformat()
    dashboard = build_dashboard(REPORT, args.datasource_uid)
    (ROOT / "dashboard.json").write_text(json.dumps(dashboard, indent=2) + "\n")
    out = ROOT / "results.json"
    out.write_text(json.dumps(REPORT, indent=2) + "\n")
    print("Evidence:", out, flush=True)
    print("Import into Grafana:", ROOT / "dashboard.json", flush=True)
