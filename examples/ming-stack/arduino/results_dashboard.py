"""Build a Grafana dashboard from recorded checks plus an InfluxDB query."""

import json
from datetime import UTC, datetime, timedelta


def build_dashboard(report, datasource_uid="ming-influxdb"):
    checks = report["checks"]
    passed = sum(check["passed"] for check in checks)
    complete = report.get("passed", len(checks) == 12 and passed == 12 and "error" not in report)
    status = "PASS" if complete else "FAILED / INCOMPLETE"
    # The runner supplies ISO times; original September evidence has only a run ID.
    started = (
        datetime.fromisoformat(report["started_at"])
        if "started_at" in report
        else datetime.strptime(report["run_id"], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    )
    finished = (
        datetime.fromisoformat(report["finished_at"]) if "finished_at" in report else started + timedelta(minutes=2)
    )
    ds = {"type": "influxdb", "uid": datasource_uid}
    query = (
        'from(bucket: "sensors")\n'
        "  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n"
        '  |> filter(fn: (r) => r._measurement == "greenhouse" and r._field == ' + json.dumps(report["field"]) + ")"
    )

    def panel(id, title, content, x, y, w, h):
        return {
            "id": id,
            "type": "text",
            "title": title,
            "gridPos": {"x": x, "y": y, "w": w, "h": h},
            "options": {"mode": "markdown", "content": content},
        }

    table = "| Check | Recorded result |\n|---|---|\n" + "\n".join(
        "| " + c["check"] + " | " + ("PASS" if c["passed"] else "FAIL") + " |" for c in checks
    )
    if not complete:
        table += "\n\nThe run stopped early. Checks not listed were **not completed**. See results.json for the error."
    return {
        "id": None,
        "uid": "uno-" + report["run_id"].lower(),
        "title": "Arduino Uno MING validation — " + report["run_id"],
        "schemaVersion": 39,
        "version": 0,
        "timezone": "browser",
        "editable": True,
        "tags": ["ming", "arduino", "hardware-validation"],
        "refresh": "",
        "time": {
            "from": (started - timedelta(seconds=5)).isoformat(),
            "to": (finished + timedelta(seconds=5)).isoformat(),
        },
        "panels": [
            panel(
                1,
                "Recorded test result",
                f"## {status} · {passed}/12 checks passed\n\n"
                "**Uno → USB host adapter → MQTT/TLS → Node-RED → InfluxDB → Grafana**\n\n"
                "Diagnostic echoes, **not sensor readings**. Recorded evidence, not a live health monitor.",
                0,
                0,
                15,
                5,
            ),
            panel(
                2,
                "Timing limitation",
                "The existing flow uses second-resolution timestamps. "
                "A rapid test stored only 2 of 3 messages; a repeat at **2-second spacing** stored all three. "
                "Same-second samples appear to overwrite each other. This example does not fix the flow.",
                15,
                0,
                9,
                5,
            ),
            {
                "id": 3,
                "type": "timeseries",
                "title": "Diagnostic values · queried from InfluxDB",
                "gridPos": {"x": 0, "y": 5, "w": 24, "h": 9},
                "datasource": ds,
                "targets": [{"refId": "A", "datasource": ds, "query": query}],
                "fieldConfig": {
                    "defaults": {
                        "displayName": "Uno echo",
                        "unit": "none",
                        "decimals": 3,
                        "custom": {
                            "drawStyle": "points",
                            "showPoints": "always",
                            "pointSize": 9,
                            "lineWidth": 0,
                        },
                    },
                    "overrides": [],
                },
                "options": {
                    "legend": {"displayMode": "list", "placement": "bottom"},
                    "tooltip": {"mode": "single"},
                },
            },
            panel(4, "Validation checks", table, 0, 14, 15, 12),
            panel(
                5,
                "Evidence and scope",
                "Run: `" + report["run_id"] + "`\n\n"
                "Topic: `" + report["topic"] + "`\n\n"
                "Raw evidence: `results.json` in the same output directory as this dashboard.\n\n"
                "Expected echo values: **314.159, 271.828, 161.803** (unitless). "
                "The chart queries this run's field in the `sensors` bucket, `greenhouse` measurement. "
                "It can be empty on another stack or after retention removes the points.\n\n"
                "The runner does not flash firmware, deploy flows, actuate GPIO, change policy "
                "or start a continuous bridge.",
                15,
                14,
                9,
                12,
            ),
        ],
    }
