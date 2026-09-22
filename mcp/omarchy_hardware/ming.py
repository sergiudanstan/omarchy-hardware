"""Operations against a MING stack: MQTT, InfluxDB, Node-RED and Grafana.

Every function here takes a target that policy.py has already resolved and
authorised; nothing in this module decides *whether* an operation is allowed.
What it does decide is *shape*:

- InfluxDB queries are built from typed parameters, never accepted as Flux. Flux
  can write (`to()`), make HTTP calls (`http.post`) and read SQL sources, so a
  raw-query tool would turn a read allowlist into a suggestion.
- Line protocol is built from typed fields with the documented escaping, so a
  value cannot smuggle in a second point or another measurement.
- Node-RED is reached only through its inject endpoint and a read of /flows.
  Deploying flows is deliberately absent: function and exec nodes make a flow
  deploy equivalent to a remote shell (docs/development-roadmap.md, non-goals).

Results never include URLs, hostnames or credentials: targets are named by the
label the user gave them in config.toml.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import stat
from collections import Counter
from typing import Any
from urllib.parse import quote, urlencode

from . import errors, http_lite, mqtt_lite
from .config import HttpSecurity, MingGrafana, MingInfluxDb, MingMqttBroker, MingNodeRed
from .errors import ToolError

MAX_SECRET_BYTES = 4096
MAX_QUERY_ROWS = 1000
MAX_MESSAGES = 100
MAX_TOPIC_BYTES = 1024  # the longest topic a received message may carry
MAX_TAGS = 16
MAX_FIELDS = 32
MAX_ANNOTATION_TEXT = 1024
MAX_DASHBOARDS = 100

# Flux duration literal: 1h, 90m, 1h30m, 2w... (Flux spec, "Duration literals").
DURATION = re.compile(r"^(?:\d+(?:ns|us|ms|mo|s|m|h|d|w|y))+$")
RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$")
AGGREGATES = frozenset({"mean", "median", "min", "max", "sum", "count", "first", "last"})
IDENTIFIER_LIMIT = 128


# --------------------------------------------------------------------------- secrets


def read_secret(env: str | None, path: str | None, label: str) -> str | None:
    """Read a credential named by the config: an environment variable or a mode-600 file."""
    if env:
        value = os.environ.get(env)
        if not value:
            raise ToolError(
                errors.CONFIG_ERROR,
                f"{label}: environment variable {env} is not set.",
                "Export it in the environment Claude Code starts from, or point the config at a *_file instead.",
            )
        return value
    if not path:
        return None
    expanded = os.path.expanduser(path)
    try:
        fd = os.open(expanded, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise ToolError(
            errors.CONFIG_ERROR,
            f"{label}: cannot open its credential file ({type(exc).__name__}).",
            "It must be a regular file, not a symlink, readable by you.",
        ) from None
    with os.fdopen(fd, "rb") as handle:
        st = os.fstat(handle.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
            raise ToolError(errors.CONFIG_ERROR, f"{label}: the credential file must be a regular file you own.")
        if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise ToolError(
                errors.CONFIG_ERROR,
                f"{label}: the credential file is readable by other users.",
                "Run chmod 600 on the file its *_file setting names.",
            )
        raw = handle.read(MAX_SECRET_BYTES + 1)
    if len(raw) > MAX_SECRET_BYTES:
        raise ToolError(errors.CONFIG_ERROR, f"{label}: the credential file is larger than a credential should be.")
    try:
        value = raw.decode("utf-8").strip("\r\n")
    except UnicodeDecodeError:
        value = ""
    if not value or any(char in value for char in "\r\n\x00"):
        raise ToolError(errors.CONFIG_ERROR, f"{label}: the credential file must hold one non-empty line.")
    return value


# --------------------------------------------------------------------------- errors


def _mqtt_failure(broker: MingMqttBroker, exc: mqtt_lite.MqttError) -> ToolError:
    text = str(exc)
    code = errors.SERVICE_UNREACHABLE if text.startswith("could not connect") else errors.SERVICE_ERROR
    hint = "Check the broker is running and the [[ming.mqtt]] security settings match it."
    if "user name or password" in text or "not authorized" in text:
        hint = "Check the username and password named under its security table, and the broker's ACL."
    return ToolError(code, f"MQTT {broker.name!r}: {text}.", hint)


def _http_failure(kind: str, name: str, exc: http_lite.HttpError) -> ToolError:
    if exc.status is None:
        return ToolError(
            errors.SERVICE_UNREACHABLE,
            f"{kind} {name!r}: {exc}.",
            "Check the service is running and its url in config.toml.",
        )
    hint = ""
    if exc.status in (401, 403):
        hint = "The API token is missing, wrong, or lacks permission for this operation."
    elif exc.status == 404:
        hint = "The resource does not exist, or the url in config.toml has the wrong path prefix."
    return ToolError(errors.SERVICE_ERROR, f"{kind} {name!r}: {exc}.", hint)


# --------------------------------------------------------------------------- MQTT


def _mqtt_options(broker: MingMqttBroker, timeout: int) -> mqtt_lite.Options:
    security = broker.security
    password = read_secret(security.password_env, security.password_file, f"MQTT {broker.name!r}")
    return mqtt_lite.Options(
        host=broker.host,
        port=broker.port,
        tls=security.tls,
        timeout=float(timeout),
        ca_file=security.ca_file,
        client_certificate=security.client_certificate,
        client_key=security.client_key,
        username=security.username,
        password=password,
    )


def _max_packet(max_payload: int) -> int:
    # Fixed topic length, the topic itself, a packet id: the payload is what varies.
    return max_payload + 2 + MAX_TOPIC_BYTES + 2


def _render_payload(payload: bytes) -> dict[str, Any]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return {"payload": payload.hex(), "encoding": "hex", "bytes": len(payload)}
    return {"payload": text, "encoding": "utf-8", "bytes": len(payload)}


def mqtt_probe(broker: MingMqttBroker, timeout: int) -> dict[str, Any]:
    try:
        with mqtt_lite.Client(_mqtt_options(broker, timeout), max_packet=_max_packet(0)):
            pass
    except mqtt_lite.MqttError as exc:
        return {"reachable": False, "error": str(exc)}
    except ToolError as exc:
        return {"reachable": False, "error": exc.message}
    return {"reachable": True}


def mqtt_subscribe(
    broker: MingMqttBroker,
    topic_filter: str,
    *,
    seconds: float,
    max_messages: int,
    timeout: int,
    max_payload: int,
) -> dict[str, Any]:
    options = _mqtt_options(broker, timeout)
    try:
        with mqtt_lite.Client(options, max_packet=_max_packet(max_payload)) as client:
            client.subscribe(topic_filter)
            received, stopped = client.collect(seconds, max_messages)
    except mqtt_lite.MqttError as exc:
        raise _mqtt_failure(broker, exc) from None

    messages = []
    for message in received:
        # The broker should only deliver what was asked for; do not rely on it.
        if not mqtt_lite.topic_matches(topic_filter, message.topic):
            continue
        entry: dict[str, Any] = {"topic": message.topic, "retained": message.retain, "qos": message.qos}
        if len(message.payload) > max_payload:
            # The packet cap allows for a long topic; the payload itself gets the configured cap.
            entry.update(bytes=len(message.payload), omitted="payload larger than [ming] max_payload_bytes")
        else:
            entry.update(_render_payload(message.payload))
        messages.append(entry)
    return {"broker": broker.name, "filter": topic_filter, "messages": messages, "stopped": stopped}


def mqtt_publish(
    broker: MingMqttBroker, topic: str, payload: bytes, *, qos: int, retain: bool, timeout: int
) -> None:
    try:
        with mqtt_lite.Client(_mqtt_options(broker, timeout), max_packet=_max_packet(0)) as client:
            client.publish(topic, payload, qos=qos, retain=retain)
    except mqtt_lite.MqttError as exc:
        raise _mqtt_failure(broker, exc) from None


# --------------------------------------------------------------------------- InfluxDB: Flux


def _text(value: object, label: str, limit: int = IDENTIFIER_LIMIT) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ToolError(errors.INVALID_ARGUMENT, f"{label} must be a non-empty string of at most {limit} characters.")
    if any(not char.isprintable() for char in value):
        raise ToolError(errors.INVALID_ARGUMENT, f"{label} must not contain control characters or newlines.")
    return value


def flux_string(value: str) -> str:
    """A Flux string literal. Backslash, quote and '${' (interpolation) are escaped."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("${", "\\${")
    return f'"{escaped}"'


def flux_time(value: str, label: str) -> str:
    """Accept 'now', a negative relative duration ('-1h') or an RFC 3339 timestamp."""
    if value == "now":
        return "now()"
    if value.startswith("-") and DURATION.match(value[1:]):
        return value
    if RFC3339.match(value):
        return f"time(v: {flux_string(value)})"
    raise ToolError(
        errors.INVALID_ARGUMENT,
        f"{label} {value!r} is not a time Flux accepts here.",
        "Use 'now', a relative duration such as -1h or -30m, or an RFC 3339 time such as 2026-09-18T10:00:00Z.",
    )


def build_query(
    bucket: str,
    measurement: str,
    *,
    field: str | None = None,
    tags: dict[str, str] | None = None,
    start: str = "-1h",
    stop: str = "now",
    aggregate: str | None = None,
    every: str | None = None,
    limit: int = 100,
) -> str:
    predicates = [f"r._measurement == {flux_string(_text(measurement, 'measurement'))}"]
    if field is not None:
        predicates.append(f"r._field == {flux_string(_text(field, 'field'))}")
    if len(tags or {}) > MAX_TAGS:
        raise ToolError(errors.INVALID_ARGUMENT, f"At most {MAX_TAGS} tag filters are accepted.")
    for key, value in sorted((tags or {}).items()):
        predicates.append(f"r[{flux_string(_text(key, 'tag key'))}] == {flux_string(_text(value, 'tag value'))}")

    lines = [
        f"from(bucket: {flux_string(bucket)})",
        f"  |> range(start: {flux_time(start, 'start')}, stop: {flux_time(stop, 'stop')})",
        f"  |> filter(fn: (r) => {' and '.join(predicates)})",
    ]
    if aggregate is not None:
        if aggregate not in AGGREGATES:
            raise ToolError(errors.INVALID_ARGUMENT, f"aggregate must be one of: {', '.join(sorted(AGGREGATES))}.")
        if every is None or not DURATION.match(every):
            raise ToolError(errors.INVALID_ARGUMENT, "An aggregate needs every= as a duration such as 1m or 15m.")
        lines.append(f"  |> aggregateWindow(every: {every}, fn: {aggregate}, createEmpty: false)")
    elif every is not None:
        raise ToolError(errors.INVALID_ARGUMENT, "every= only applies together with aggregate=.")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_QUERY_ROWS:
        raise ToolError(errors.INVALID_ARGUMENT, f"limit must be an integer in 1-{MAX_QUERY_ROWS}.")
    # One more than asked for, so a full page can be told from a cut one.
    lines.append(f"  |> limit(n: {limit + 1})")
    return "\n".join(lines)


def build_schema_query(bucket: str, measurement: str | None, start: str) -> str:
    flux_start = flux_time(start, "start")
    header = 'import "influxdata/influxdb/schema"\n'
    if measurement is None:
        return header + f"schema.measurements(bucket: {flux_string(bucket)}, start: {flux_start})"
    m = flux_string(_text(measurement, "measurement"))
    b = flux_string(bucket)
    return header + (
        f'fields = schema.measurementFieldKeys(bucket: {b}, measurement: {m}, start: {flux_start})\n'
        f'  |> set(key: "kind", value: "field")\n'
        f"tags = schema.measurementTagKeys(bucket: {b}, measurement: {m}, start: {flux_start})\n"
        f'  |> set(key: "kind", value: "tag")\n'
        f"union(tables: [fields, tags])"
    )


# Where parse_csv records the InfluxDB datatype of a row's _value column.
VALUE_TYPE = "#datatype"


def parse_csv(text: str, max_rows: int) -> list[dict[str, str]]:
    """Parse InfluxDB's annotated CSV (header plus the datatype annotation).

    Tables are separated by blank lines, each preceded by a '#datatype' row. The
    type of `_value` is kept under VALUE_TYPE: plain CSV prints the float 21.0 as
    "21", and a model that reads it back as an integer would then write an
    integer into a float field, which InfluxDB rejects.
    """
    rows: list[dict[str, str]] = []
    header: list[str] | None = None
    types: dict[str, str] = {}
    datatypes: list[str] = []
    for line in csv.reader(io.StringIO(text)):
        if not any(cell.strip() for cell in line):
            header, types, datatypes = None, {}, []
            continue
        if line[0] == "#datatype":
            datatypes = line
            continue
        if line[0].startswith("#"):
            continue  # other annotations (#group, #default) are not requested
        if header is None:
            header = line
            types = dict(zip(header, datatypes, strict=False))
            continue
        row = dict(zip(header, line, strict=False))
        # InfluxDB reports a failed query as a table whose only columns are error
        # and reference. A tag that happens to be called "error" is data.
        if {column for column in header if column} <= {"error", "reference"} and row.get("error"):
            raise http_lite.HttpError(f"the query failed: {' '.join(row['error'].split())[:200]}", 200)
        if "_value" in types:
            row[VALUE_TYPE] = types["_value"]
        rows.append(row)
        if len(rows) >= max_rows:
            break
    return rows


def _flux(db: MingInfluxDb, query: str, timeout: int, max_rows: int) -> list[dict[str, str]]:
    token = read_secret(db.security.token_env, db.security.token_file, f"InfluxDB {db.name!r}")
    headers = {"Content-Type": "application/json", "Accept": "application/csv"}
    if token:
        headers["Authorization"] = f"Token {token}"
    dialect = {"header": True, "annotations": ["datatype"]}
    body = json.dumps({"query": query, "type": "flux", "dialect": dialect}).encode()
    try:
        response = http_lite.request(
            "POST",
            f"{db.url}/api/v2/query?{urlencode({'org': db.org})}",
            headers=headers,
            body=body,
            timeout=timeout,
            ca_file=db.security.ca_file,
        )
        return parse_csv(response.body.decode("utf-8", errors="replace"), max_rows)
    except http_lite.HttpError as exc:
        raise _http_failure("InfluxDB", db.name, exc) from None


def _float(value: str) -> Any:
    number = float(value)
    # NaN and infinities are not JSON; hand them back as the text InfluxDB sent.
    return number if math.isfinite(number) else value


def _typed(value: str, datatype: str | None) -> Any:
    """Convert a CSV cell using InfluxDB's datatype annotation; guess only when it is absent."""
    try:
        if datatype == "double":
            return _float(value)
        if datatype in ("long", "unsignedLong"):
            return int(value)
        if datatype == "boolean":
            return value == "true"
        if datatype is not None:
            return value
        for convert in (int, _float):
            try:
                return convert(value)
            except ValueError:
                continue
    except ValueError:
        return value
    if value in ("true", "false"):
        return value == "true"
    return value


INTERNAL_COLUMNS = frozenset({"", "result", "table", VALUE_TYPE})


def influx_query(db: MingInfluxDb, query: str, *, timeout: int, limit: int) -> tuple[list[dict[str, Any]], bool]:
    """Up to `limit` points, and whether more matched (the query asks for one extra)."""
    rows = _flux(db, query, timeout, limit + 1)
    points = []
    for row in rows[:limit]:
        points.append(
            {
                "time": row.get("_time"),
                "measurement": row.get("_measurement"),
                "field": row.get("_field"),
                "value": _typed(row.get("_value", ""), row.get(VALUE_TYPE)),
                "tags": {k: v for k, v in row.items() if not k.startswith("_") and k not in INTERNAL_COLUMNS},
            }
        )
    return points, len(rows) > limit


def influx_schema(db: MingInfluxDb, query: str, *, timeout: int, by_kind: bool) -> dict[str, list[str]]:
    rows = _flux(db, query, timeout, MAX_QUERY_ROWS)
    if not by_kind:
        return {"measurements": sorted({row.get("_value", "") for row in rows} - {""})}
    fields = sorted({row["_value"] for row in rows if row.get("kind") == "field" and row.get("_value")})
    tags = sorted(
        {row["_value"] for row in rows if row.get("kind") == "tag" and row.get("_value")}
        - {"_measurement", "_field", "_start", "_stop"}
    )
    return {"fields": fields, "tags": tags}


def influx_probe(db: MingInfluxDb, timeout: int) -> dict[str, Any]:
    try:
        health = http_lite.request("GET", f"{db.url}/health", timeout=timeout, ca_file=db.security.ca_file).json()
    except http_lite.HttpError as exc:
        return {"reachable": False, "error": str(exc)}
    if not isinstance(health, dict):
        return {"reachable": True}
    return {"reachable": True, "status": health.get("status"), "version": health.get("version")}


# --------------------------------------------------------------------------- InfluxDB: line protocol


def _lp(value: str, special: str, label: str) -> str:
    value = _text(value, label)
    escaped = value.replace("\\", "\\\\")
    for char in special:
        escaped = escaped.replace(char, "\\" + char)
    return escaped


def _field_value(value: object, label: str) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        if not -(2**63) <= value < 2**63:
            raise ToolError(errors.INVALID_ARGUMENT, f"{label} does not fit a 64-bit integer.")
        return f"{value}i"
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ToolError(errors.INVALID_ARGUMENT, f"{label} must be a finite number.")
        return repr(value)
    if isinstance(value, str):
        if any(char in value for char in "\n\r\x00"):
            raise ToolError(errors.INVALID_ARGUMENT, f"{label} must be a single line.")
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    raise ToolError(errors.INVALID_ARGUMENT, f"{label} must be a number, boolean or string.")


def build_line(
    measurement: str,
    fields: dict[str, object],
    tags: dict[str, str] | None = None,
    timestamp: int | None = None,
) -> bytes:
    """One point of InfluxDB line protocol, with its documented escaping."""
    if measurement.startswith("_"):
        raise ToolError(errors.INVALID_ARGUMENT, "Measurement names starting with '_' are reserved by InfluxDB.")
    if measurement.startswith("#"):
        # Line protocol treats the whole line as a comment: InfluxDB answers 204 and stores nothing.
        raise ToolError(errors.INVALID_ARGUMENT, "Measurement names cannot start with '#'.")
    if not isinstance(fields, dict) or not fields or len(fields) > MAX_FIELDS:
        raise ToolError(errors.INVALID_ARGUMENT, f"fields must hold 1-{MAX_FIELDS} entries.")
    tags = tags or {}
    if not isinstance(tags, dict) or len(tags) > MAX_TAGS:
        raise ToolError(errors.INVALID_ARGUMENT, f"tags must hold at most {MAX_TAGS} entries.")

    head = _lp(measurement, ", ", "measurement")
    for key, value in sorted(tags.items()):
        if key.startswith("_"):
            raise ToolError(errors.INVALID_ARGUMENT, "Tag keys starting with '_' are reserved by InfluxDB.")
        head += f",{_lp(key, ',= ', 'tag key')}={_lp(value, ',= ', 'tag value')}"
    parts = []
    for key, value in sorted(fields.items()):
        if key.startswith("_"):
            raise ToolError(errors.INVALID_ARGUMENT, "Field keys starting with '_' are reserved by InfluxDB.")
        parts.append(f"{_lp(key, ',= ', 'field key')}={_field_value(value, f'field {key!r}')}")
    line = f"{head} {','.join(parts)}"
    if timestamp is not None:
        if not isinstance(timestamp, int) or isinstance(timestamp, bool) or timestamp < 0:
            raise ToolError(errors.INVALID_ARGUMENT, "timestamp must be a non-negative integer of Unix seconds.")
        line += f" {timestamp}"
    return line.encode("utf-8")


def influx_write(db: MingInfluxDb, bucket: str, line: bytes, *, timeout: int) -> None:
    token = read_secret(db.security.token_env, db.security.token_file, f"InfluxDB {db.name!r}")
    headers = {"Content-Type": "text/plain; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Token {token}"
    query = urlencode({"org": db.org, "bucket": bucket, "precision": "s"})
    try:
        http_lite.request(
            "POST",
            f"{db.url}/api/v2/write?{query}",
            headers=headers,
            body=line,
            timeout=timeout,
            ca_file=db.security.ca_file,
        )
    except http_lite.HttpError as exc:
        raise _http_failure("InfluxDB", db.name, exc) from None


# --------------------------------------------------------------------------- Node-RED


def _bearer(security: HttpSecurity, label: str) -> dict[str, str]:
    token = read_secret(security.token_env, security.token_file, label)
    return {"Authorization": f"Bearer {token}"} if token else {}


def nodered_flows(nodered: MingNodeRed, *, timeout: int) -> dict[str, Any]:
    """Summarise the deployed flows: tabs, node counts by type, and inject nodes.

    Function-node source and node configuration are left out on purpose: they
    are large, and they are the part of a flow that should not steer the model.
    """
    label = f"Node-RED {nodered.name!r}"
    headers = {"Node-RED-API-Version": "v1", "Accept": "application/json", **_bearer(nodered.security, label)}
    try:
        nodes = http_lite.request(
            "GET", f"{nodered.url}/flows", headers=headers, timeout=timeout, ca_file=nodered.security.ca_file
        ).json()
    except http_lite.HttpError as exc:
        raise _http_failure("Node-RED", nodered.name, exc) from None
    if not isinstance(nodes, list):
        raise ToolError(errors.SERVICE_ERROR, f"{label}: /flows did not return a list of nodes.")

    nodes = [node for node in nodes if isinstance(node, dict)]
    tabs = [
        {"id": node.get("id"), "label": node.get("label"), "disabled": bool(node.get("disabled"))}
        for node in nodes
        if node.get("type") == "tab"
    ]
    tab_labels = {tab["id"]: tab["label"] for tab in tabs}
    injects = [
        {
            "id": node.get("id"),
            "name": node.get("name") or None,
            "flow": tab_labels.get(node.get("z")),
            "allowlisted": node.get("id") in nodered.inject_nodes,
        }
        for node in nodes
        if node.get("type") == "inject"
    ]
    counts = Counter(str(node.get("type")) for node in nodes if node.get("type") not in ("tab", "subflow"))
    return {
        "nodered": nodered.name,
        "flows": tabs,
        "node_counts": dict(sorted(counts.items())),
        "inject_nodes": injects,
    }


def nodered_inject(nodered: MingNodeRed, node_id: str, *, timeout: int) -> None:
    headers = _bearer(nodered.security, f"Node-RED {nodered.name!r}")
    try:
        http_lite.request(
            "POST",
            f"{nodered.url}/inject/{quote(node_id, safe='')}",
            headers=headers,
            body=b"",
            timeout=timeout,
            ca_file=nodered.security.ca_file,
        )
    except http_lite.HttpError as exc:
        raise _http_failure("Node-RED", nodered.name, exc) from None


def nodered_probe(nodered: MingNodeRed, timeout: int) -> dict[str, Any]:
    try:
        headers = _bearer(nodered.security, f"Node-RED {nodered.name!r}")
        settings = http_lite.request(
            "GET", f"{nodered.url}/settings", headers=headers, timeout=timeout, ca_file=nodered.security.ca_file
        ).json()
    except http_lite.HttpError as exc:
        return {"reachable": False, "error": str(exc)}
    except ToolError as exc:
        return {"reachable": False, "error": exc.message}
    return {"reachable": True, "version": settings.get("version") if isinstance(settings, dict) else None}


# --------------------------------------------------------------------------- Grafana


def grafana_dashboards(grafana: MingGrafana, *, query: str | None, limit: int, timeout: int) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"type": "dash-db", "limit": limit}
    if query:
        params["query"] = _text(query, "query")
    headers = {"Accept": "application/json", **_bearer(grafana.security, f"Grafana {grafana.name!r}")}
    try:
        results = http_lite.request(
            "GET",
            f"{grafana.url}/api/search?{urlencode(params)}",
            headers=headers,
            timeout=timeout,
            ca_file=grafana.security.ca_file,
        ).json()
    except http_lite.HttpError as exc:
        raise _http_failure("Grafana", grafana.name, exc) from None
    if not isinstance(results, list):
        raise ToolError(errors.SERVICE_ERROR, f"Grafana {grafana.name!r}: search did not return a list.")
    return [
        {
            "uid": item.get("uid"),
            "title": item.get("title"),
            "folder": item.get("folderTitle"),
            "tags": item.get("tags") or [],
        }
        for item in results[:limit]
        if isinstance(item, dict)
    ]


def build_annotation(
    text: str,
    *,
    tags: list[str] | None = None,
    dashboard_uid: str | None = None,
    panel_id: int | None = None,
    time_ms: int | None = None,
) -> bytes:
    body: dict[str, Any] = {"text": _text(text, "text", MAX_ANNOTATION_TEXT)}
    if tags:
        if not isinstance(tags, list) or len(tags) > MAX_TAGS:
            raise ToolError(errors.INVALID_ARGUMENT, f"tags must be a list of at most {MAX_TAGS} strings.")
        body["tags"] = [_text(tag, "tag", 64) for tag in tags]
    if dashboard_uid is not None:
        body["dashboardUID"] = _text(dashboard_uid, "dashboard_uid", 64)
    if panel_id is not None:
        if dashboard_uid is None:
            raise ToolError(errors.INVALID_ARGUMENT, "panel_id needs dashboard_uid.")
        if not isinstance(panel_id, int) or isinstance(panel_id, bool) or panel_id < 0:
            raise ToolError(errors.INVALID_ARGUMENT, "panel_id must be a non-negative integer.")
        body["panelId"] = panel_id
    if time_ms is not None:
        if not isinstance(time_ms, int) or isinstance(time_ms, bool) or time_ms < 0:
            raise ToolError(errors.INVALID_ARGUMENT, "time_ms must be non-negative Unix milliseconds.")
        body["time"] = time_ms
    return json.dumps(body, separators=(",", ":")).encode()


def grafana_annotate(grafana: MingGrafana, body: bytes, *, timeout: int) -> Any:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        **_bearer(grafana.security, f"Grafana {grafana.name!r}"),
    }
    try:
        answer = http_lite.request(
            "POST",
            f"{grafana.url}/api/annotations",
            headers=headers,
            body=body,
            timeout=timeout,
            ca_file=grafana.security.ca_file,
        ).json()
    except http_lite.HttpError as exc:
        raise _http_failure("Grafana", grafana.name, exc) from None
    return answer.get("id") if isinstance(answer, dict) else None


def grafana_probe(grafana: MingGrafana, timeout: int) -> dict[str, Any]:
    try:
        health = http_lite.request(
            "GET", f"{grafana.url}/api/health", timeout=timeout, ca_file=grafana.security.ca_file
        ).json()
    except http_lite.HttpError as exc:
        return {"reachable": False, "error": str(exc)}
    if not isinstance(health, dict):
        return {"reachable": True}
    return {"reachable": True, "database": health.get("database"), "version": health.get("version")}
