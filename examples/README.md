# Examples

Sketches checked on physical hardware. Each folder is an Arduino sketch
(folder name matches the `.ino`), so it works with `compile_sketch` and
`arduino-cli` as is.

| Example | Board | FQBN |
| --- | --- | --- |
| [`stm32-servo-sweep`](stm32-servo-sweep/stm32-servo-sweep.ino) | STM32 Nucleo-F411RE, servo on D3 | `STMicroelectronics:stm32:Nucleo_64:pnum=NUCLEO_F411RE` |

```bash
arduino-cli compile --upload -p /dev/ttyACM0 \
  --fqbn STMicroelectronics:stm32:Nucleo_64:pnum=NUCLEO_F411RE \
  examples/stm32-servo-sweep
```

`stm32-servo-sweep` needs the `STMicroelectronics:stm32` core. Set `MAX_ANGLE`
to your servo's mechanical limit before flashing; every write is clamped to it.

## MING stack

[`ming-stack`](ming-stack) is not a sketch: it is a Docker Compose file for the
MQTT, InfluxDB, Node-RED and Grafana services the `ming_*`, `mqtt_*`,
`influx_*`, `nodered_*` and `grafana_*` tools talk to. Run it on a Pi (64-bit
OS) or on the Omarchy machine itself:

```bash
cd examples/ming-stack
MING_BIND=0.0.0.0 MING_HOSTNAMES="pi.local" ./bootstrap.sh
```

See [`ming-stack/README.md`](ming-stack/README.md). Its
[`demo/`](ming-stack/demo/README.md) runs a simulated greenhouse with a Grafana
dashboard and Omarchy notifications, for showing Claude operate the stack.
