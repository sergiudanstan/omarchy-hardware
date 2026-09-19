---
description: Plan and check the wiring for the chosen project before any code
---

Plan the wiring for the chosen project on the board in CLAUDE.md.

1. Call `board_profile` for the board. Pick pins only from its `pins` list, by
   capability. Skip every `reserved_pins` entry and explain any `caution_pins`
   entry you use.
2. Write a table with one row per connection: board pin, part and part pin,
   signal type, and a note.
3. Check each point and state the result:
   - **Logic level:** the part's voltage against the board's `logic_voltage`. Add
     a level shifter or divider where needed.
   - **Current:** the load against `current_ma.per_pin_recommended`. Anything larger
     goes through a transistor or driver.
   - **Power:** which rail feeds each part, whether that rail can supply it, and a
     common ground.
   - **I2C:** pull-ups present, and no two devices on the same address.
   - **Inductive loads:** a flyback diode.
4. Run `wiring_check` with the same connections, each with a role, part_voltage,
   load, current_ma, driver, i2c_address and pull_up where they apply. Fix every
   error and explain every warning. Re-run it until the verdict is `pass`, or
   until every remaining warning is one the user accepts knowingly.
5. List what the user should check with a multimeter before plugging in power.

Ask the user to confirm the wiring is done before `/hw-build`.

$ARGUMENTS
