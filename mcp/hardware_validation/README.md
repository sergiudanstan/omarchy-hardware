# Hardware validation runs

Scripts here drive the real MCP server over stdio against physical hardware. They are
not part of `pytest` and CI never runs them.

## Arduino Uno — `run_uno.py`

**This flashes the board.** Whatever sketch is on it will be replaced.

1. Copy `hw-validation/` to a sketch root, e.g. `~/Arduino/hw-validation`.
2. In `~/.config/omarchy-hardware/config.toml`, set `[flash] allow = true` and
   `sketch_roots = ["~/Arduino/hw-validation"]`.
3. Make any other sketch directory to test the `sketch_roots` refusal, e.g.
   `mkdir -p /tmp/outside && cp hw-validation/hw-validation.ino /tmp/outside/outside.ino`.
4. Run it with a Python that has the `mcp` package (the plugin venv or a dev venv):

```bash
python mcp/hardware_validation/run_uno.py \
  --launcher bin/hardware-mcp \
  --sketch ~/Arduino/hw-validation \
  --outside-sketch /tmp/outside \
  --out uno-results.json
```

It exits non-zero if any check fails. USB serials, by-id paths and upload tokens are
redacted in the output. Record results in
[`docs/hardware-validation.md`](../../docs/hardware-validation.md), and turn flashing
back off afterwards if you don't need it.
