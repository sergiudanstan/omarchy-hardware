---
description: Find out what is wired to the board by flashing the read-only I2C probe
---

Find out which I2C devices are connected to the board in CLAUDE.md.

1. The probe **replaces the firmware on the board**. Call `board_history` for the port,
   tell the user what is on the board now, and say that it will be overwritten. On an
   ESP32, offer `firmware_backup` first; `firmware_restore` can put it back later.
   Other boards have no backup, so say so. Wait for their go-ahead.
2. The probe sketch is `./omarchy_probe/omarchy_probe.ino`. It only reads: it scans
   the bus and reads chip-ID registers, and never writes to a device. Call
   `compile_sketch` on it with the board's FQBN and port. Then call `upload_sketch`
   with `confirm=true`.
3. Call `serial_open` on the port at 115200. Then call `serial_expect` with mode
   `json` and match `{"probe": "done"}`, waiting up to 10 s. The report repeats every
   5 seconds.
4. Pass the report's `i2c` list to `identify_i2c`.
5. Report back:
   - the parts that are confirmed and the ones that are only possible;
   - which default I2C pins the bus used, from `board_profile`;
   - any address clash, such as a TCA9548A and a BME280 both answering on 0x76.

   If nothing answered, check pull-ups, power, SDA/SCL swapped, and 3.3 V against
   5 V.
6. Offer to add the confirmed parts to parts.toml. The user edits it, not you. Then
   offer `/hw-propose`.

$ARGUMENTS
