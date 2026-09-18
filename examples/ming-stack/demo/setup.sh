#!/usr/bin/env bash
# Add the greenhouse demo to a stack started by ../bootstrap.sh.
#
#   1. an MQTT user and a write-only InfluxDB token for Node-RED,
#   2. the Node-RED "Greenhouse" flow: MQTT -> InfluxDB bridge and fan buttons,
#   3. the simulated greenhouse device (compose profile "demo"),
#   4. the Grafana "Greenhouse" dashboard (provisioned from ../grafana).
#
# --annotate also promotes Claude's Grafana service account to Editor so it
# can mark events on the dashboard (provisioned dashboards stay read-only).
# Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077

ANNOTATE=false
[[ ${1:-} == --annotate ]] && ANNOTATE=true

die() { printf 'demo: %s\n' "$*" >&2; exit 1; }
step() { printf '\n==> %s\n' "$*"; }
json() { python3 -c "import json, sys; print(json.load(sys.stdin)$1)"; }
[[ -s .env && -s secrets/influxdb-admin-token ]] || die "run ../bootstrap.sh first"

BIND=$(sed -n 's/^MING_BIND=//p' .env)
PROBE=${BIND:-127.0.0.1}
[[ $PROBE == 0.0.0.0 ]] && PROBE=127.0.0.1
ORG=$(sed -n 's/^MING_ORG=//p' .env)
ORG=${ORG:-home}

# --- MQTT user for Node-RED ------------------------------------------------------

step "MQTT user 'nodered'"
[[ -s secrets/mqtt-nodered-password ]] || openssl rand -hex 24 >secrets/mqtt-nodered-password
if ! grep -q '^nodered:' secrets/mosquitto.passwd; then
  # Hash just the new line, then append it: -U on the whole file would hash
  # the existing hashes a second time.
  printf 'nodered:%s\n' "$(<secrets/mqtt-nodered-password)" >secrets/.nodered.passwd
  docker compose run --rm --no-deps --user "$(id -u):$(id -g)" -v "$PWD/secrets:/work" \
    --entrypoint mosquitto_passwd mosquitto -U /work/.nodered.passwd </dev/null
  cat secrets/.nodered.passwd >>secrets/mosquitto.passwd
  rm -f secrets/.nodered.passwd
fi
# SIGHUP reloads the password file and the ACL (mosquitto/acl already lists nodered).
docker compose kill -s HUP mosquitto >/dev/null </dev/null
echo "    read sensors/#, write actuators/#"

# --- InfluxDB token for Node-RED --------------------------------------------------

step "InfluxDB token for the Node-RED bridge"
influx() {
  INFLUX_TOKEN=$(<secrets/influxdb-admin-token) docker compose exec -T \
    -e INFLUX_TOKEN -e INFLUX_HOST=https://localhost:8086 -e INFLUX_ORG="$ORG" \
    -e SSL_CERT_FILE=/etc/ming/certs/ca.crt influxdb influx "$@" </dev/null
}
if [[ ! -s secrets/influxdb-nodered-token ]]; then
  sensors=$(influx bucket list --json | python3 -c '
import json, sys
print(next(b["id"] for b in json.load(sys.stdin) if b["name"] == "sensors"))')
  influx auth create --description "node-red greenhouse bridge" --json --write-bucket "$sensors" \
    | json '["token"]' >secrets/influxdb-nodered-token
fi
echo "    write sensors only"

# --- Node-RED flow ---------------------------------------------------------------

step "Node-RED 'Greenhouse' flow"
admin=$(printf 'data-urlencode = "password=%s"\n' "$(<secrets/nodered-admin-password)" \
  | curl -fsS --config - --cacert certs/ca.crt "https://$PROBE:1880/auth/token" \
    -d client_id=node-red-admin -d grant_type=password -d scope='*' -d username=admin \
  | json '["access_token"]')
python3 - demo/flow.json secrets/mqtt-nodered-password secrets/influxdb-nodered-token >secrets/.flow.json <<'PY'
import json, sys
flow, mqtt_password, influx_token = sys.argv[1:4]
data = json.load(open(flow))
secrets = {
    "ming0broker": {"user": "nodered", "password": open(mqtt_password).read().strip()},
    "gh0write": {"user": "", "password": open(influx_token).read().strip()},
}
for node in data["configs"] + data["nodes"]:
    if node["id"] in secrets:
        node["credentials"] = secrets[node["id"]]
json.dump(data, sys.stdout)
PY
# The session token goes to curl from a mode-600 file, not argv.
printf 'Authorization: Bearer %s\n' "$admin" >secrets/.nodered-auth
printf '{"token":"%s"}' "$admin" >secrets/.nodered-revoke
trap 'rm -f secrets/.nodered-auth secrets/.nodered-revoke secrets/.flow.json' EXIT
nodered() {
  curl -fsS --cacert certs/ca.crt -H @secrets/.nodered-auth -H 'Content-Type: application/json' "$@"
}
# Node-RED assigns the tab its own id on import, so find it by the bridge node.
tab=$(nodered -H 'Node-RED-API-Version: v1' "https://$PROBE:1880/flows" | python3 -c '
import json, sys
print(next((n.get("z", "") for n in json.load(sys.stdin) if n.get("id") == "gh0in"), ""))')
if [[ -n $tab ]]; then
  nodered -X PUT --data @secrets/.flow.json "https://$PROBE:1880/flow/$tab" >/dev/null
  echo "    updated"
else
  nodered -X POST --data @secrets/.flow.json "https://$PROBE:1880/flow" >/dev/null
  echo "    created"
fi
rm -f secrets/.flow.json
# Revoke the admin session; it was only needed for the import.
nodered -X POST --data @secrets/.nodered-revoke "https://$PROBE:1880/auth/revoke" >/dev/null || true

# --- device, dashboard, Grafana role ------------------------------------------------

step "Simulated greenhouse device"
docker compose --profile demo up -d greenhouse </dev/null

step "Grafana 'Greenhouse' dashboard"
docker compose restart grafana >/dev/null </dev/null
for _ in $(seq 60); do
  curl -fsS --cacert certs/ca.crt "https://$PROBE:3000/api/health" >/dev/null 2>&1 && break
  sleep 2
done
if $ANNOTATE; then
  grafana_api() {
    printf 'user = "admin:%s"\n' "$(<secrets/grafana-admin-password)" \
      | curl -fsS --config - --cacert certs/ca.crt -X "$1" -H 'Content-Type: application/json' \
        ${3:+--data "$3"} "https://$PROBE:3000$2"
  }
  account=$(grafana_api GET "/api/serviceaccounts/search?query=claude" "" \
    | python3 -c 'import json, sys; print(next(a["id"] for a in json.load(sys.stdin)["serviceAccounts"] if a["name"] == "claude"))')
  grafana_api PATCH "/api/serviceaccounts/$account" '{"role":"Editor"}' >/dev/null
  echo "    Claude's service account is now Editor (can annotate)"
fi

cat <<EOF

Greenhouse demo running. It warms by about 2.4 C a minute until the fan runs.

In ~/.config/omarchy-hardware/config.toml on the Omarchy machine, set:
  [[ming.mqtt]]      publish = ["actuators/fan"]
  [[ming.nodered]]   inject_nodes = ["fan0on", "fan0off"]
EOF
$ANNOTATE && echo '  [[ming.grafana]]   annotate = true'
cat <<EOF

Dashboard: https://$PROBE:3000/d/greenhouse
Then follow demo/README.md.
EOF
