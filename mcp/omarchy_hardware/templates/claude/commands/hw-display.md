---
description: Identify, wire, and test a display on the board
---

Bring up a display on the board in CLAUDE.md.

1. Call `board_profile` for the board. If its `peripherals` includes a `display`,
   use that profile entry and its `libraries`. Otherwise, ask which display
   module is wired and call `display_reference` for its controller.
2. Plan the connections from the profile or catalog roles, then call
   `wiring_check` with those connections. Fix every error and explain every
   warning before writing firmware.
3. Tell the user that the test sketch will replace the current firmware. On an
   ESP32, offer `firmware_backup`. Wait for the user's go-ahead before uploading.
4. Write a small display test sketch using the constructor lines from the board
   profile, not from memory. Its first serial line must be
   `{"fw":"<sketch_name>","build":"<__DATE__ __TIME__>"}`, followed by a
   self-test JSON line naming the display controller and resolution.
5. Call `compile_sketch` with the board's FQBN. After it compiles, get the
   user's go-ahead if they have not already given it, then call `upload_sketch`
   with `confirm=true`.
6. Read the serial lines with `serial_open` and `serial_expect`. Ask the user
   what the screen shows; you cannot see it. Only say the display works after
   the user confirms what appeared.

$ARGUMENTS
