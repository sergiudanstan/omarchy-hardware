#!/usr/bin/env python3
"""Desktop notifications for the greenhouse demo, on the Omarchy machine.

Watches the greenhouse through the plugin's own [ming] configuration and MQTT
client -- the same broker, credentials and TLS settings Claude uses -- and calls
notify-send when the fan switches or the temperature crosses the alarm line.
Works whether the stack runs locally or on a Pi.

Run it with the plugin's Python:

    ~/.local/share/omarchy-hardware/venv/bin/python demo/notify.py [broker-name]
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
import time

from omarchy_hardware import config, mqtt_lite, policy
from omarchy_hardware.errors import ToolError
from omarchy_hardware.ming import _mqtt_options

HOT = 30.0  # notify above this ...
COOL = 28.0  # ... and again only after dropping below this
WINDOW = 5.0  # seconds per connection: how quickly a change shows up


def notify(title: str, body: str, urgency: str = "normal") -> None:
    print(f"{time.strftime('%H:%M:%S')}  {title}: {body}", flush=True)
    subprocess.run(  # noqa: S603
        ["notify-send", "--app-name=MING greenhouse", f"--urgency={urgency}", title, body],  # noqa: S607
        check=False,
    )


def main() -> None:
    settings = config.load()
    try:
        broker = policy.ming_mqtt(settings, sys.argv[1] if len(sys.argv) > 1 else None)
    except ToolError as exc:
        sys.exit(f"{exc.message} {exc.hint}")
    options = _mqtt_options(broker, settings.ming_timeout)

    fan: str | None = None
    hot = False
    print(f"watching sensors/greenhouse/# on broker {broker.name!r}; Ctrl+C to stop")
    while True:
        try:
            with mqtt_lite.Client(options, max_packet=4096) as client:
                client.subscribe("sensors/greenhouse/#")
                messages, _ = client.collect(WINDOW, 1000)
        except mqtt_lite.MqttError as exc:
            print(f"broker: {exc}; retrying", file=sys.stderr)
            time.sleep(5)
            continue
        values = [(m.topic, m.payload.decode("utf-8", errors="replace").strip()) for m in messages]
        # Announce where the fan ended up in this batch, not every flicker on the way.
        latest_fan = next((value for topic, value in reversed(values) if topic.endswith("/fan")), None)
        if latest_fan is not None and latest_fan != fan:
            if fan is not None:  # the first value is the current state, not a change
                notify(
                    f"Greenhouse fan {latest_fan.upper()}",
                    "Cooling started." if latest_fan == "on" else "Fan stopped.",
                )
            fan = latest_fan
        for topic, value in values:
            if not topic.endswith("/temperature"):
                continue
            try:
                temp = float(value)
            except ValueError:
                continue
            if not hot and temp >= HOT:
                hot = True
                notify("Greenhouse too hot", f"{temp:.1f} °C and rising. Ask Claude to cool it.", "critical")
            elif hot and temp < COOL:
                hot = False
                notify("Greenhouse back to normal", f"{temp:.1f} °C")

if __name__ == "__main__":
    # Ctrl+C is how the demo is stopped; exit quietly instead of with a traceback.
    with contextlib.suppress(KeyboardInterrupt):
        main()
