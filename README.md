# Hardware — Arduino & Raspberry Pi for Omarchy

An Omarchy bar widget that shows the development boards plugged into your machine,
plus an MCP server that lets Claude Code actually talk to them.

Omarchy ships no serial support and no MCP servers, so this adds both:

- **In the bar** — connected Arduino / ESP32 / Pico boards, their ports, and whether
  they're accessible.
- **In Claude Code** — 18 tools for enumerating boards, reading and writing serial,
  compiling and flashing sketches, and driving Raspberry Pi GPIO pins over SSH.

## Requirements

| Dependency | Needed for | Installed by |
|---|---|---|
| Python 3.11+ | everything | already on Omarchy |
| `mcp`, `pyserial` | the MCP server | `setup.sh`, into `~/.local/share/omarchy-hardware/venv` |
| membership of `uucp` | serial access | `setup.sh` (`sudo usermod`) |
| `arduino-cli` | compiling and flashing | `setup.sh`, via `omarchy-mise-install` |
| `openssh` | Raspberry Pi GPIO | already on Omarchy |
| Claude Code | the MCP tools | already on Omarchy |

Serial monitoring and board listing work without `arduino-cli`. GPIO needs a Pi you can
already reach over SSH with key-based login.

## Install

```bash
omarchy plugin add https://github.com/sergiudanstan/omarchy-hardware --enable
~/.config/omarchy/plugins/io.github.sergiudanstan.hardware/bin/setup.sh
```

Then **log out and back in** — group membership only applies to new login sessions. The
panel will say so until you do.

`setup.sh` is idempotent; re-run it any time. `--check` prints the current state as JSON
without changing anything, and the panel's "Run setup" button just opens it in a
terminal so you can watch what it does.

## Remove

```bash
claude mcp remove omarchy-hardware
omarchy plugin remove io.github.sergiudanstan.hardware
rm -rf ~/.config/omarchy-hardware ~/.local/share/omarchy-hardware
```

Optionally `sudo gpasswd -d "$USER" uucp` to give up serial access again. Nothing else
is left behind — the plugin installs no udev rules, systemd units, or sudoers entries.

## What it can do

**Boards** — `list_boards`, `describe_board`

**Serial** — `serial_open`, `serial_status`, `serial_read`, `serial_write`,
`serial_query`, `serial_clear`, `serial_close`, `list_sessions`

The server holds ports open between tool calls and drains them into a 256 KB ring
buffer in the background, so reads never block on a silent device — every read has a
deadline, hard-capped at 10 seconds.

**Flashing** — `list_fqbns`, `compile_sketch`, `upload_sketch`

**Raspberry Pi GPIO** — `pi_status`, `gpio_list_pins`, `gpio_read_pin`, `gpio_set_mode`,
`gpio_write_pin`

## Configuration

`~/.config/omarchy-hardware/config.toml`, created by `setup.sh` with mode `600`:

```toml
[pi]
hosts = ["raspberrypi.local"]   # empty by default; GPIO tools are inert until set
allowed_pins = [2, 3, 4, ...]   # BCM 0 and 1 excluded (HAT ID EEPROM)
ssh_timeout = 10

[serial]
max_write_bytes = 4096
write_budget_bytes_per_min = 65536

[flash]
allow = true
```

The file is refused if it's group- or world-readable, since it names the hosts the
plugin may reach.

## Security

Omarchy plugins run **unsandboxed inside the shell process**, so it's fair to want to
know exactly what this one does. In full:

- **`sudo` is used once**, in `setup.sh`, for `usermod -aG uucp $USER`. Nothing else in
  the plugin ever escalates. The panel's setup button opens a terminal rather than a
  graphical polkit prompt, so you see the command and its output.
- **Serial paths are allowlisted.** Only `/dev/ttyACM*`, `/dev/ttyUSB*` and
  `/dev/serial/by-id/*` are accepted, and the path is re-checked *after* symlink
  resolution, so a symlinked device can't redirect a write to `/dev/sda`. `/dev/ttyS*`
  is deliberately excluded because those are often serial consoles.
- **There is no "run a command on the Pi" tool.** GPIO builds fixed `pinctrl` /
  `raspi-gpio` argument lists with `shell=False`. The host must be in your config
  allowlist and the pin must be in your allowed pin list.
- **Flashing is double-gated.** `upload_sketch` needs both a token minted by a
  successful `compile_sketch` in the same server process and an explicit `confirm=true`,
  and it refuses boards it cannot identify. Every upload is logged to
  `~/.local/state/omarchy-hardware/flash.log`.
- **Writes are capped** per call and rate-limited per port.
- **The MCP server never runs as root** and never calls `sudo`.

Read `bin/setup.sh` before running it — it's commented for exactly that purpose.

## Status

Board detection, the serial stack, and the safety layer are covered by tests that run
without any hardware (26 tests, using PTY pairs). **Flashing and Raspberry Pi GPIO are
implemented but not yet verified against physical hardware** — treat them as
experimental and report what breaks.

## Development

```bash
~/.local/share/omarchy-hardware/venv/bin/python -m pytest mcp/tests/ -q   # no board needed
omarchy plugin validate .
omarchy-shell shell rescanPlugins
```

## License

MIT — see [LICENSE](LICENSE).
