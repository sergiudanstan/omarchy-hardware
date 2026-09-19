---
description: Write, compile, flash (with confirmation) and verify the firmware
---

Build and verify the chosen project on the board in CLAUDE.md.

1. Write `./<sketch_name>/<sketch_name>.ino`, using only pins from the wiring plan.
   After `Serial.begin`, the first line it prints is
   `{"fw":"<sketch_name>","build":"<__DATE__ __TIME__>"}`. Then it prints a
   self-test line, `{"selftest":true}` or `{"selftest":false,"reason":"..."}`.
   Keep the rest of the output line-based, and use JSON lines for readings.
2. Call `compile_sketch` with the board's FQBN and port. Fix any errors.
3. Tell the user what will be flashed and to which board, and wait for their
   go-ahead. Then call `upload_sketch` with the token, artifact and `confirm=true`.
   If flashing is refused by config, explain what the user would change, and do
   not change it.
4. Call `serial_open` on the port at the sketch's baud rate. Then use
   `serial_expect` with mode `json`, first for `{"fw":"<sketch_name>"}`, then for
   `{"selftest":true}`.
5. If a check fails or times out, read the returned context lines. If they hold a
   crash (`Guru Meditation Error`, `abort()`, `Backtrace:`), pass those lines to
   `decode_crash` to get the function and source line. Find the cause in the code
   or the wiring, and fix it. Ask again before flashing again.
6. When it passes, summarise what runs on the board. If it has no label yet,
   offer to name it with `board_label`.

$ARGUMENTS
