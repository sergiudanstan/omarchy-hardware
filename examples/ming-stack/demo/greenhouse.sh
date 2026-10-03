#!/bin/sh
# A simulated greenhouse controller. It warms up in the sun and cools while the
# fan runs, reports over MQTT as user `device`, and obeys actuators/fan.
#
#   sensors/greenhouse/temperature   degrees C, every INTERVAL seconds
#   sensors/greenhouse/humidity      percent
#   sensors/greenhouse/fan           on | off (retained)
set -eu
umask 077

# Mosquitto 2.0 reads these default config files. Keep credentials out of
# process arguments (including short-lived publishers) and the environment.
# mktemp creates a private directory; the configs are mode 600 via umask.
XDG_CONFIG_HOME=$(mktemp -d /tmp/greenhouse.XXXXXX)
export XDG_CONFIG_HOME
trap 'rm -rf "$XDG_CONFIG_HOME"' EXIT
PASS=$(cat /run/secrets/mqtt-device-password)
for client in mosquitto_sub mosquitto_pub; do
  printf -- '-P %s\n' "$PASS" >"$XDG_CONFIG_HOME/$client"
done
unset PASS
FAN=$XDG_CONFIG_HOME/fan
echo off >"$FAN"

mqtt() {
  cmd=$1
  shift
  "$cmd" -h mosquitto -p 8883 --cafile /etc/ming/certs/ca.crt -u device "$@"
}

# Follow fan commands in the background, reconnecting whenever the broker
# goes away; anything but on/off is ignored.
while :; do
  mqtt mosquitto_sub -t actuators/fan | while read -r command; do
    case $command in
      on | off) echo "$command" >"$FAN" ;;
    esac
  done
  sleep 2
done &

temp=${START_TEMP:-27.0}
while :; do
  fan=$(cat "$FAN")
  temp=$(awk -v t="$temp" -v f="$fan" -v seed="$(date +%s)" 'BEGIN {
    srand(seed)
    t += (f == "on" ? -0.35 : 0.2) + (rand() - 0.5) * 0.1
    if (t < 19) t = 19
    if (t > 38) t = 38
    printf "%.2f", t
  }')
  humidity=$(awk -v t="$temp" 'BEGIN { printf "%.1f", 90 - t * 1.5 }')
  # A reading lost while the broker restarts is skipped, not fatal.
  mqtt mosquitto_pub -t sensors/greenhouse/temperature -m "$temp" || true
  mqtt mosquitto_pub -t sensors/greenhouse/humidity -m "$humidity" || true
  mqtt mosquitto_pub -t sensors/greenhouse/fan -m "$fan" -r || true
  sleep "${INTERVAL:-5}"
done
