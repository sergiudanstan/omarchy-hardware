"""Full validation of the example MING stack and the plugin's ten MING tools.

Not part of pytest. It needs examples/ming-stack bootstrapped and running with the
demo profile (the simulated greenhouse), `docker` usable by this user, and a
config.toml whose [ming] targets point at that stack. It checks three layers:

- the stack itself: containers, ports, image pins, TLS versions, where secrets
  and the CA key are visible;
- the services' own authentication and ACLs, independent of the plugin;
- every MING tool through the real MCP server, including its refusals, and the
  end-to-end paths greenhouse -> MQTT -> Node-RED -> InfluxDB and
  mqtt_publish / nodered_inject -> fan -> greenhouse.

The greenhouse readings are simulated by examples/ming-stack/demo/greenhouse.sh.
They are demo data, not measurements from a sensor.

    sg docker -c "python mcp/hardware_validation/run_ming.py \\
        --launcher bin/hardware-mcp --stack examples/ming-stack --out ming.json"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

SERVICES = ("mosquitto", "influxdb", "nodered", "grafana", "greenhouse")
TLS_PORTS = {"mosquitto": 8883, "influxdb": 8086, "nodered": 1880, "grafana": 3000}
PASS, FAIL, LIMIT = "pass", "fail", "limitation"


class Checks:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def add(self, check_id: str, layer: str, name: str, passed: bool | str, evidence: Any) -> None:
        status = passed if isinstance(passed, str) else (PASS if passed else FAIL)
        self.items.append({"id": check_id, "layer": layer, "check": name, "status": status, "evidence": evidence})
        print(f"{status.upper():11} {check_id}  {name}", file=sys.stderr)


def sh(*argv: str, cwd: Path | None = None, timeout: int = 60, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - fixed argv built from constants and the stack path
        argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False, input=stdin
    )


def compose(stack: Path, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return sh("docker", "compose", "--profile", "demo", *args, cwd=stack, timeout=timeout, stdin="")


async def tool(session: ClientSession, name: str, **args: Any) -> dict[str, Any]:
    result = await session.call_tool(name, args)
    payload = result.structured_content
    if payload is None:
        payload = json.loads("".join(getattr(block, "text", "") for block in result.content))
    if set(payload) == {"result"} and isinstance(payload["result"], dict):
        payload = payload["result"]
    return payload


def code(payload: dict[str, Any]) -> str | None:
    return (payload.get("error") or {}).get("code")


def infrastructure(checks: Checks, stack: Path) -> None:
    listed = compose(stack, "ps", "--format", "json")
    rows = [json.loads(line) for line in listed.stdout.splitlines() if line.strip().startswith("{")]
    state = {row["Service"]: row.get("State") for row in rows}
    checks.add("MING-I-1", "stack", "All five services are running", all(state.get(s) == "running" for s in SERVICES),
               state)

    published = {}
    for row in rows:
        for pub in row.get("Publishers") or []:
            if pub.get("PublishedPort"):
                published[f'{row["Service"]}:{pub["PublishedPort"]}'] = pub.get("URL")
    loopback_only = bool(published) and all(url in ("127.0.0.1", "::1") for url in published.values())
    checks.add("MING-I-2", "stack", "Published ports bind to loopback only", loopback_only, published)

    spec = (stack / "compose.yaml").read_text(encoding="utf-8")
    pinned = re.findall(r"image:\s*(\S+)", spec)
    unpinned = [image for image in pinned if "@sha256:" not in image]
    running = {}
    for row in rows:
        digest = sh("docker", "inspect", "--format", "{{index .RepoDigests 0}}", row["Image"]).stdout.strip()
        running[row["Service"]] = digest.split("@")[-1] if "@" in digest else digest
    wanted = {m.group(1): m.group(2) for m in re.finditer(r"image:\s*([^@\s]+)@(sha256:[0-9a-f]{64})", spec)}
    mismatched = {s: d for s, d in running.items() if d not in wanted.values()}
    checks.add("MING-I-3", "stack", "Images are pinned by digest and the running images match",
               not unpinned and not mismatched, {"unpinned": unpinned, "mismatched": mismatched})

    tls = {}
    for service, port in TLS_PORTS.items():
        result = {}
        for version, flag in (("1.1", "-tls1_1"), ("1.2", "-tls1_2"), ("1.3", "-tls1_3")):
            probe = sh("openssl", "s_client", "-connect", f"127.0.0.1:{port}", flag, "-cipher", "DEFAULT:@SECLEVEL=0",
                       "-CAfile", str(stack / "certs" / "ca.crt"), "-verify_return_error", timeout=15, stdin="")
            # A failed handshake still prints the attempted protocol in the session
            # summary; only "New, TLSv1.x, Cipher is <suite>" means one was agreed.
            match = re.search(r"^New, (TLSv[\d.]+), Cipher is (?!\(NONE\))", probe.stdout, re.M)
            verified = "Verify return code: 0 (ok)" in probe.stdout
            result[version] = match.group(1) if match and verified else "refused"
        tls[service] = result
    ok = all(r["1.1"] == "refused" and r["1.2"] == "TLSv1.2" and r["1.3"] == "TLSv1.3" for r in tls.values())
    checks.add("MING-I-4", "stack", "TLS 1.1 refused; 1.2 and 1.3 verified against the stack CA", ok, tls)

    readable = {}
    for service in ("mosquitto", "influxdb", "nodered", "grafana"):
        probe = compose(stack, "exec", "-T", service, "sh", "-c",
                        "for f in /etc/ming/certs/ca.key /etc/ming/ca/ca.key; do [ -r $f ] && echo $f; done; true")
        readable[service] = probe.stdout.split()
    checks.add("MING-I-5", "stack", "No container can read the CA private key", not any(readable.values()), readable)

    exposed = {}
    for row in rows:
        env = sh("docker", "inspect", "--format", "{{range .Config.Env}}{{println .}}{{end}}", row["Name"]).stdout
        names = [line.split("=", 1)[0] for line in env.splitlines()
                 if re.search(r"(PASSWORD|TOKEN|SECRET)[A-Z_]*=", line) and not line.split("=", 1)[0].endswith("FILE")]
        if names:
            exposed[row["Service"]] = names
    checks.add("MING-I-6", "stack", "No secret value is passed in a container's environment", not exposed, exposed)

    loose = []
    for folder in ("certs", "secrets"):
        for path in [stack / folder, *sorted((stack / folder).iterdir())]:
            if path.name.endswith(".crt"):
                continue
            if path.stat().st_mode & 0o004:
                loose.append(str(path.relative_to(stack)))
    checks.add("MING-I-7", "stack", "Keys and secrets are not world-readable on the host", not loose, {"loose": loose})


def broker(checks: Checks, stack: Path, plugin: Path) -> None:
    sys.path.insert(0, str(plugin / "mcp"))
    from omarchy_hardware import mqtt_lite

    ca = str(stack / "certs" / "ca.crt")
    device_password = (stack / "secrets" / "mqtt-device-password").read_text().strip()
    claude_password = (stack / "secrets" / "mqtt-password").read_text().strip()

    def attempt(**kw: Any) -> str:
        options = mqtt_lite.Options(host="127.0.0.1", port=8883, tls=True, timeout=5, ca_file=ca, **kw)
        try:
            with mqtt_lite.Client(options, max_packet=4096):
                return "connected"
        except mqtt_lite.MqttError as exc:
            return f"refused: {exc}"

    wrong = "not-the-password"  # noqa: S105 - deliberately wrong
    outcomes = {"anonymous": attempt(), "wrong password": attempt(username="claude", password=wrong),
                "claude": attempt(username="claude", password=claude_password),
                "device": attempt(username="device", password=device_password)}
    ok = (outcomes["anonymous"].startswith("refused") and outcomes["wrong password"].startswith("refused")
          and outcomes["claude"] == outcomes["device"] == "connected")
    checks.add("MING-M-1", "services", "Mosquitto refuses anonymous and wrong-password clients", ok, outcomes)

    def leaks(publisher: tuple[str, str], topic: str, listener: tuple[str, str], topic_filter: str) -> list[str]:
        """Publish as one user while another listens; what arrives is what the ACL let through."""
        listen = mqtt_lite.Options(host="127.0.0.1", port=8883, tls=True, timeout=5, ca_file=ca,
                                   username=listener[0], password=listener[1])
        speak = mqtt_lite.Options(host="127.0.0.1", port=8883, tls=True, timeout=5, ca_file=ca,
                                  username=publisher[0], password=publisher[1])
        marker = f"acl-probe-{time.time_ns()}"
        with mqtt_lite.Client(listen, max_packet=4096) as sub:
            sub.subscribe(topic_filter)
            with mqtt_lite.Client(speak, max_packet=4096) as pub:
                pub.publish(topic, marker.encode(), qos=0, retain=False)
            messages, _ = sub.collect(2.0, 50)
        return [m.payload.decode(errors="replace") for m in messages if marker in m.payload.decode(errors="replace")]

    device = ("device", device_password)
    claude = ("claude", claude_password)
    device_to_actuator = leaks(device, "actuators/acl-probe", claude, "actuators/#")
    # The device user may not read sensors/#, so the listener for claude's forbidden
    # write is claude itself (it may read sensors/#).
    claude_to_sensors = leaks(claude, "sensors/acl-probe", claude, "sensors/#")
    checks.add("MING-M-2", "services", "Broker ACL: devices cannot command actuators; claude cannot fake readings",
               not device_to_actuator and not claude_to_sensors,
               {"device -> actuators/#": len(device_to_actuator), "claude -> sensors/#": len(claude_to_sensors)})


async def plugin_tools(checks: Checks, args: argparse.Namespace, stack: Path, diagnostics: dict[str, Any]) -> None:
    params = StdioServerParameters(command=args.launcher, args=[])
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()

        status = await tool(session, "ming_status")
        reachable = {kind: [t.get("reachable") for t in status.get(kind, [])]
                     for kind in ("mqtt", "influxdb", "nodered", "grafana")}
        checks.add("MING-T-1", "plugin", "ming_status reaches all four services",
                   all(all(v) and v for v in reachable.values()), reachable)

        readings = await tool(session, "mqtt_subscribe", topic_filter="sensors/greenhouse/+", seconds=12,
                              max_messages=20)
        received = [{"topic": m.get("topic"), "payload": m.get("payload")} for m in readings.get("messages", [])]
        diagnostics["greenhouse_readings"] = {"kind": "simulated demo readings, not sensor measurements",
                                              "messages": received}
        topics = {m["topic"] for m in received}
        checks.add("MING-T-2", "plugin", "mqtt_subscribe receives the greenhouse's temperature and humidity",
                   {"sensors/greenhouse/temperature", "sensors/greenhouse/humidity"} <= topics,
                   {"messages": len(received), "topics": sorted(topics)})

        refused = {
            "publish outside the allowlist": code(await tool(session, "mqtt_publish", topic="actuators/pump",
                                                             payload="on", confirm=True)),
            "publish to a wildcard": code(await tool(session, "mqtt_publish", topic="actuators/#", payload="on",
                                                     confirm=True)),
            "subscribe wider than allowed": code(await tool(session, "mqtt_subscribe", topic_filter="#", seconds=1)),
            "publish without confirm": code(await tool(session, "mqtt_publish", topic="actuators/fan",
                                                       payload="on")),
        }
        checks.add("MING-T-3", "plugin", "MQTT refusals: allowlist, wildcards, filter width, confirmation",
                   all(refused.values()), refused)

        fan_on = await tool(session, "mqtt_publish", topic="actuators/fan", payload="on", confirm=True)
        fan_state = await tool(session, "mqtt_subscribe", topic_filter="sensors/greenhouse/fan", seconds=8,
                               max_messages=5)
        states = [m.get("payload") for m in fan_state.get("messages", [])]
        checks.add("MING-T-4", "plugin", "mqtt_publish actuators/fan=on is obeyed by the greenhouse",
                   fan_on.get("ok") is True and "on" in states, {"published": fan_on.get("ok"), "fan_reports": states})

        measurements = await tool(session, "influx_measurements", bucket="sensors")
        checks.add("MING-T-5", "plugin", "influx_measurements lists greenhouse in the sensors bucket",
                   "greenhouse" in (measurements.get("measurements") or []), measurements.get("measurements"))

        recent = await tool(session, "influx_query", bucket="sensors", measurement="greenhouse", field="temperature",
                            start="-2m", limit=20)
        points = recent.get("points") or []
        diagnostics["influx_recent_temperature"] = {"kind": "simulated demo readings, not sensor measurements",
                                                    "points": points[-5:]}
        checks.add("MING-T-6", "plugin", "influx_query returns greenhouse temperatures from the last 2 minutes",
                   recent.get("ok") is True and len(points) >= 3, {"points": len(points), "truncated":
                                                                  recent.get("truncated")})

        marker = time.time_ns() % 1_000_000
        wrote = await tool(session, "influx_write", bucket="claude", measurement="validation",
                           fields={"run": marker}, tags={"source": "run_ming"}, confirm=True)
        back = await tool(session, "influx_query", bucket="claude", measurement="validation", field="run",
                          start="-5m", limit=50)
        values = [p.get("value") for p in back.get("points") or []]
        checks.add("MING-T-7", "plugin", "influx_write to the claude bucket reads back", wrote.get("ok") is True
                   and marker in values, {"written": marker, "found": marker in values})

        denied = {
            "write to sensors (read-only bucket)": code(await tool(session, "influx_write", bucket="sensors",
                                                                   measurement="x", fields={"v": 1}, confirm=True)),
            "measurement starting with #": code(await tool(session, "influx_write", bucket="claude",
                                                           measurement="#x", fields={"v": 1}, confirm=True)),
            "query of an unlisted bucket": code(await tool(session, "influx_query", bucket="_monitoring",
                                                           measurement="x")),
        }
        checks.add("MING-T-8", "plugin", "InfluxDB refusals: bucket scope and line-protocol comments",
                   all(denied.values()), denied)

        flows = await tool(session, "nodered_flows")
        names = json.dumps(flows)
        checks.add("MING-T-9", "plugin", "nodered_flows shows the greenhouse flow and its inject nodes",
                   flows.get("ok") is True and "fan0on" in names and "fan0off" in names,
                   {"ok": flows.get("ok")})

        inject_off = await tool(session, "nodered_inject", node_id="fan0off", confirm=True)
        after = await tool(session, "mqtt_subscribe", topic_filter="sensors/greenhouse/fan", seconds=8,
                           max_messages=5)
        states = [m.get("payload") for m in after.get("messages", [])]
        checks.add("MING-T-10", "plugin", "nodered_inject fan0off switches the greenhouse fan off",
                   inject_off.get("ok") is True and "off" in states, {"fan_reports": states})
        unlisted = code(await tool(session, "nodered_inject", node_id="gh0in", confirm=True))
        checks.add("MING-T-11", "plugin", "nodered_inject refuses a node outside inject_nodes", bool(unlisted),
                   unlisted)

        dashboards = await tool(session, "grafana_dashboards")
        titles = [d.get("title") for d in dashboards.get("dashboards") or []]
        checks.add("MING-T-12", "plugin", "grafana_dashboards lists the provisioned greenhouse dashboard",
                   any("reenhouse" in (t or "") for t in titles), titles)
        note = await tool(session, "grafana_annotate", text=f"run_ming validation {marker}", tags=["validation"],
                          confirm=True)
        checks.add("MING-T-13", "plugin", "grafana_annotate writes an annotation", note.get("ok") is True,
                   {k: note.get(k) for k in ("ok", "id") if k in note} or note)


def end_to_end(checks: Checks, stack: Path, plugin: Path) -> None:
    """Node-RED must write only well-formed greenhouse points; a device must not inject others."""
    sys.path.insert(0, str(plugin / "mcp"))
    from omarchy_hardware import mqtt_lite

    ca = str(stack / "certs" / "ca.crt")
    device = (stack / "secrets" / "mqtt-device-password").read_text().strip()
    marker = f"injected{time.time_ns() % 1_000_000}"
    options = mqtt_lite.Options(host="127.0.0.1", port=8883, tls=True, timeout=5, ca_file=ca,
                                username="device", password=device)
    with mqtt_lite.Client(options, max_packet=4096) as client:
        client.publish("sensors/greenhouse/temperature", f"21.5\n{marker} value=1".encode(), qos=0, retain=False)
    time.sleep(3)
    token = (stack / "secrets" / "influxdb-admin-token").read_text().strip()
    flux = f'from(bucket: "sensors") |> range(start: -5m) |> filter(fn: (r) => r._measurement == "{marker}")'
    query = sh("curl", "-sS", "--cacert", ca, "-H", f"Authorization: Token {token}", "-H", "Accept: application/csv",
               "-H", "Content-Type: application/vnd.flux", "--data-binary", flux,
               "https://127.0.0.1:8086/api/v2/query?org=home")
    injected = marker in query.stdout
    checks.add("MING-E-1", "end-to-end", "A device cannot inject extra InfluxDB points through the demo flow",
               not injected, {"payload": f"21.5\\n{marker} value=1", "extra_measurement_stored": injected})

    api = sh("curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", "--cacert", ca, "https://127.0.0.1:1880/flows")
    token_nr = (stack / "secrets" / "nodered-token").read_text().strip()
    deploy = sh("curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", "--cacert", ca, "-X", "POST",
                "-H", f"Authorization: Bearer {token_nr}", "-H", "Content-Type: application/json",
                "--data", "[]", "https://127.0.0.1:1880/flows")
    grafana = sh("curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", "--cacert", ca,
                 "https://127.0.0.1:3000/api/search")
    ok = api.stdout == "401" and deploy.stdout in ("401", "403") and grafana.stdout == "401"
    checks.add("MING-E-2", "services", "Admin APIs refuse anonymous access; the claude token cannot deploy flows",
               ok, {"nodered GET /flows anonymous": api.stdout, "nodered POST /flows with claude token":
                    deploy.stdout, "grafana /api/search anonymous": grafana.stdout})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--launcher", required=True)
    parser.add_argument("--stack", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    stack = Path(args.stack).expanduser().resolve()
    plugin = Path(args.launcher).expanduser().resolve().parents[1]
    checks = Checks()
    diagnostics: dict[str, Any] = {}
    for step in (lambda: infrastructure(checks, stack), lambda: broker(checks, stack, plugin),
                 lambda: asyncio.run(plugin_tools(checks, args, stack, diagnostics)),
                 lambda: end_to_end(checks, stack, plugin)):
        try:
            step()
        except Exception as exc:  # noqa: BLE001 - record why a layer stopped; keep the others
            checks.add("MING-X", "runner", "A validation layer completed", False, f"{type(exc).__name__}: {exc}"[:500])
    counts = {s: sum(c["status"] == s for c in checks.items) for s in (PASS, FAIL, LIMIT)}
    result = {"date": time.strftime("%Y-%m-%d"), "summary": counts, "checks": checks.items,
              "diagnostics": diagnostics,
              "plugin_commit": sh("git", "rev-parse", "HEAD", cwd=plugin).stdout.strip(),
              "images": re.findall(r"image:\s*(\S+)", (stack / "compose.yaml").read_text(encoding="utf-8"))}
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(counts, file=sys.stderr)
    sys.exit(1 if counts[FAIL] else 0)


if __name__ == "__main__":
    os.umask(0o077)
    main()
