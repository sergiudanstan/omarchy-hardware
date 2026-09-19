---
description: Propose projects for the connected board using the parts on hand
---

Propose 3 to 5 projects for the board described in CLAUDE.md.

1. Call `board_profile` for the board, `parts_inventory()`, and `board_history` for
   its port. If the board has a label or earlier uploads, say what it was used for
   and offer to continue that work as one of the options.
2. Rank the projects by how much they use parts the user already has. Put the ones
   that need nothing new first.
3. For each project give:
   - one sentence on what it does;
   - the parts it uses from the inventory, and anything missing;
   - the pins it needs by type (I2C, PWM, analog, digital), checked against the
     profile's capabilities;
   - any voltage or current issue, such as a 5 V part on a 3.3 V board, or a servo
     or relay that needs a separate supply;
   - its difficulty and the self-test it can run.
4. If a MING stack is configured (`ming_status`), note which projects could publish
   readings to it.

Then ask which one to build. Do not write code yet.

$ARGUMENTS
