#!/usr/bin/env bash
# Bring up the example MING stack with TLS and least-privilege credentials.
#
# Safe to re-run: certificates, secrets and tokens that already exist are kept.
#
# Environment:
#   MING_BIND              address the ports listen on (default 127.0.0.1;
#                          0.0.0.0 to reach this stack from another machine)
#   MING_HOSTNAMES         extra names/IPs for the TLS certificate, space
#                          separated, e.g. "pi.local 192.168.1.20"
#   MING_ORG               InfluxDB organisation (default home)
#   MING_GRAFANA_ANNOTATE  1 to let Claude add Grafana annotations (Editor role)
#   MING_CLIENT_DIR        if set, install the client files there as well, e.g.
#                          ~/.config/omarchy-hardware/ming on this machine
set -euo pipefail
cd "$(dirname "$0")"
umask 077

BIND=${MING_BIND:-127.0.0.1}
NAMES=${MING_HOSTNAMES:-}
MING_HOSTNAMES_ORIG=$NAMES
ORG=${MING_ORG:-home}
ANNOTATE=${MING_GRAFANA_ANNOTATE:-0}
CLIENT_DIR=${MING_CLIENT_DIR:-}

# The users the images run as. Each needs to read its own key or secret through
# a bind mount; ACLs grant exactly that instead of making the files world-readable.
UID_MOSQUITTO=1883
UID_INFLUXDB=1000
UID_NODERED=1000
UID_GRAFANA=472

step() { printf '\n==> %s\n' "$*"; }
die() { printf 'bootstrap: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null || die "missing $1 -- $2"; }

need docker "install Docker Engine and the compose plugin"
need openssl "install openssl"
need curl "install curl"
need python3 "install python3"
need setfacl "install the acl package; it lets the containers read their keys without making them world-readable"
docker compose version >/dev/null 2>&1 || die "docker compose is not available"
docker info >/dev/null 2>&1 || die "cannot reach the Docker daemon (are you in the docker group?)"

PROBE=$BIND
[[ $PROBE == 0.0.0.0 ]] && PROBE=127.0.0.1
json() { python3 -c "import json, sys; print(json.load(sys.stdin)$1)"; }

# --- certificates --------------------------------------------------------------

step "TLS certificates"
mkdir -p certs secrets client
REISSUED=false
if [[ -s certs/server.crt ]]; then
  echo "    keeping certs/ (delete it to issue new ones)"
else
  REISSUED=true
  host=$(hostname)
  san="DNS:localhost,IP:127.0.0.1,DNS:mosquitto,DNS:influxdb,DNS:nodered,DNS:grafana,DNS:$host"
  [[ $host == *.* ]] || san+=",DNS:$host.local"
  [[ $BIND == 0.0.0.0 || $BIND == 127.0.0.1 ]] || NAMES="$NAMES $BIND"
  # Name constraints: the CA can only vouch for these names and addresses, so
  # trusting it in a browser (demo/README.md) cannot put any other site at risk
  # even if certs/ca.key leaks.
  permit="permitted;DNS:localhost,permitted;DNS:mosquitto,permitted;DNS:influxdb"
  permit+=",permitted;DNS:nodered,permitted;DNS:grafana,permitted;DNS:$host"
  permit+=",permitted;IP:127.0.0.0/255.0.0.0"
  [[ $host == *.* ]] || permit+=",permitted;DNS:$host.local"
  for name in $NAMES; do
    if [[ $name =~ ^[0-9.]+$ ]]; then
      san+=",IP:$name"
      permit+=",permitted;IP:$name/255.255.255.255"
    elif [[ $name == *:* ]]; then
      san+=",IP:$name"
      permit+=",permitted;IP:$name/ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff"
    else
      san+=",DNS:$name"
      permit+=",permitted;DNS:$name"
    fi
  done
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 3650 \
    -subj "/CN=MING example CA ($host)" -keyout certs/ca.key -out certs/ca.crt \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -addext "nameConstraints=critical,$permit" 2>/dev/null
  openssl req -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -subj "/CN=$host" \
    -keyout certs/server.key -out certs/server.csr 2>/dev/null
  openssl x509 -req -in certs/server.csr -CA certs/ca.crt -CAkey certs/ca.key -CAcreateserial \
    -days 825 -out certs/server.crt 2>/dev/null \
    -extfile <(printf 'subjectAltName=%s\nextendedKeyUsage=serverAuth\nbasicConstraints=critical,CA:FALSE\n' "$san")
  rm -f certs/server.csr certs/ca.srl
  echo "    issued for $san"
fi
chmod 644 certs/ca.crt certs/server.crt

# --- secrets -------------------------------------------------------------------

step "Secrets"
for name in influxdb-admin-password influxdb-admin-token grafana-admin-password \
  mqtt-password mqtt-device-password nodered-admin-password nodered-token nodered-credential-secret; do
  [[ -s secrets/$name ]] || openssl rand -hex 24 >"secrets/$name"
done

cat >.env <<EOF
MING_BIND=$BIND
MING_ORG=$ORG
NODE_RED_ADMIN_PASSWORD=$(<secrets/nodered-admin-password)
NODE_RED_API_TOKEN=$(<secrets/nodered-token)
NODE_RED_CREDENTIAL_SECRET=$(<secrets/nodered-credential-secret)
EOF

# Placeholder so the bind mount exists before the token is minted further down.
[[ -e secrets/influxdb-grafana-token ]] || : >secrets/influxdb-grafana-token

if [[ ! -s secrets/mosquitto.passwd ]]; then
  # Written in plain text, then hashed in place, so no password reaches argv.
  printf 'claude:%s\ndevice:%s\n' "$(<secrets/mqtt-password)" "$(<secrets/mqtt-device-password)" \
    >secrets/mosquitto.passwd
  docker compose run --rm --no-deps --user "$(id -u):$(id -g)" -v "$PWD/secrets:/work" \
    --entrypoint mosquitto_passwd mosquitto -U /work/mosquitto.passwd
fi
echo "    secrets/ is mode 700; nothing in it is world-readable"

grant() {
  local uid=$1
  shift
  setfacl -m "u:$uid:x" certs secrets
  local file
  for file in "$@"; do setfacl -m "u:$uid:r" "$file"; done
}
grant "$UID_MOSQUITTO" certs/server.key secrets/mosquitto.passwd
grant "$UID_INFLUXDB" certs/server.key secrets/influxdb-admin-password secrets/influxdb-admin-token
grant "$UID_NODERED" certs/server.key
grant "$UID_GRAFANA" certs/server.key secrets/grafana-admin-password secrets/influxdb-grafana-token

# --- services ------------------------------------------------------------------

wait_for() {
  local url=$1 label=$2
  for _ in $(seq 90); do
    curl -fsS --cacert certs/ca.crt "$url" >/dev/null 2>&1 && return
    sleep 2
  done
  die "$label did not come up at $url; see: docker compose logs"
}

step "Starting Mosquitto, InfluxDB and Node-RED"
docker compose up -d mosquitto influxdb nodered
# Services already running still hold the old certificates.
if $REISSUED; then
  docker compose --profile demo restart >/dev/null
fi
wait_for "https://$PROBE:8086/health" InfluxDB

step "InfluxDB bucket and scoped tokens"
influx() {
  INFLUX_TOKEN=$(<secrets/influxdb-admin-token) docker compose exec -T \
    -e INFLUX_TOKEN -e INFLUX_HOST=https://localhost:8086 -e INFLUX_ORG="$ORG" \
    -e SSL_CERT_FILE=/etc/ming/certs/ca.crt influxdb influx "$@"
}
bucket_id() {
  influx bucket list --json | python3 -c '
import json, sys
print(next((b["id"] for b in json.load(sys.stdin) if b["name"] == sys.argv[1]), ""))' "$1"
}
[[ -n $(bucket_id claude) ]] || influx bucket create --name claude --retention 0 >/dev/null
sensors=$(bucket_id sensors)
claude=$(bucket_id claude)
[[ -n $sensors && -n $claude ]] || die "could not find the sensors and claude buckets"
if [[ ! -s secrets/influxdb-token ]]; then
  influx auth create --description "omarchy-hardware (claude)" --json \
    --read-bucket "$sensors" --read-bucket "$claude" --write-bucket "$claude" \
    | json '["token"]' >secrets/influxdb-token
fi
if [[ ! -s secrets/influxdb-grafana-token ]]; then
  influx auth create --description "grafana datasource" --json \
    --read-bucket "$sensors" --read-bucket "$claude" \
    | json '["token"]' >secrets/influxdb-grafana-token
fi
echo "    claude: read sensors + claude, write claude. grafana: read only."

step "Starting Grafana"
docker compose up -d grafana
wait_for "https://$PROBE:3000/api/health" Grafana

grafana_api() {
  # The admin password goes to curl on stdin, not argv.
  printf 'user = "admin:%s"\n' "$(<secrets/grafana-admin-password)" \
    | curl -fsS --config - --cacert certs/ca.crt -X "$1" -H 'Content-Type: application/json' \
      ${3:+--data "$3"} "https://$PROBE:3000$2"
}
role=Viewer
[[ $ANNOTATE == 1 ]] && role=Editor
if [[ ! -s secrets/grafana-token ]]; then
  # Reuse the account if an earlier run created it but stopped before the token.
  account=$(grafana_api GET "/api/serviceaccounts/search?query=claude" "" \
    | python3 -c 'import json, sys; print(next((a["id"] for a in json.load(sys.stdin)["serviceAccounts"] if a["name"] == "claude"), ""))')
  [[ -n $account ]] ||
    account=$(grafana_api POST /api/serviceaccounts "{\"name\":\"claude\",\"role\":\"$role\"}" | json '["id"]')
  grafana_api POST "/api/serviceaccounts/$account/tokens" '{"name":"omarchy-hardware"}' \
    | json '["key"]' >secrets/grafana-token
fi
echo "    service account 'claude' with role $role"

# --- client bundle ---------------------------------------------------------------

step "Client files for the Omarchy machine"
cp certs/ca.crt client/ming-ca.pem
cp secrets/influxdb-token client/influxdb-token
cp secrets/grafana-token client/grafana-token
cp secrets/mqtt-password client/mqtt-password
cp secrets/nodered-token client/nodered-token
chmod 600 client/*

read -r target _ <<<"$MING_HOSTNAMES_ORIG"
if [[ -z $target ]]; then
  if [[ $BIND == 127.0.0.1 ]]; then target=127.0.0.1; else target=$(hostname); fi
fi
# Literal on purpose: written into config.toml, where the plugin expands it.
# shellcheck disable=SC2088
dir='~/.config/omarchy-hardware/ming'
annotate=false
[[ $ANNOTATE == 1 ]] && annotate=true

cat >client/ming.toml <<EOF
# Append to ~/.config/omarchy-hardware/config.toml on the Omarchy machine.
[ming]
allow = true

[[ming.mqtt]]
name = "stack"
host = "$target"
port = 8883
subscribe = ["sensors/#", "actuators/#"]
publish = []                     # exact topics, e.g. ["actuators/fan"]
security = { ca_file = "$dir/ming-ca.pem", username = "claude", password_file = "$dir/mqtt-password" }

[[ming.influxdb]]
name = "stack"
url = "https://$target:8086"
org = "$ORG"
read_buckets = ["sensors", "claude"]
write_buckets = ["claude"]
security = { ca_file = "$dir/ming-ca.pem", token_file = "$dir/influxdb-token" }

[[ming.nodered]]
name = "stack"
url = "https://$target:1880"
inject_nodes = []                # ids from the nodered_flows tool
security = { ca_file = "$dir/ming-ca.pem", token_file = "$dir/nodered-token" }

[[ming.grafana]]
name = "stack"
url = "https://$target:3000"
annotate = $annotate
security = { ca_file = "$dir/ming-ca.pem", token_file = "$dir/grafana-token" }
EOF

if [[ -n $CLIENT_DIR ]]; then
  mkdir -p "$CLIENT_DIR"
  chmod 700 "$CLIENT_DIR"
  cp client/ming-ca.pem client/*-token client/mqtt-password "$CLIENT_DIR"/
  chmod 600 "$CLIENT_DIR"/*
  echo "    installed into $CLIENT_DIR"
fi

cat <<EOF

Done. Editor logins (user admin):
  Node-RED  https://$target:1880   password in secrets/nodered-admin-password
  Grafana   https://$target:3000   password in secrets/grafana-admin-password
  InfluxDB  https://$target:8086   password in secrets/influxdb-admin-password
Devices publish to MQTT as user 'device' (secrets/mqtt-device-password) on sensors/#.

For the plugin, copy client/ to $dir on the Omarchy machine
(mode 700, files 600), then append client/ming.toml to config.toml.
EOF
