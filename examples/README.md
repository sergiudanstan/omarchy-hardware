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
