# Ask Claude about your hardware from Slack

Tag the bridge's Slack app in a channel, for example `@omarchy-hardware what's
plugged in, and which pins does the Uno use for I2C?`. It answers in the thread,
using the boards attached to this workstation.

## Why a bridge, and not Claude's own Slack app

Claude's Slack app (Claude Tag) runs every session in Anthropic's cloud sandbox.
From there it can reach neither the USB ports of this machine nor the
omarchy-hardware MCP server, which runs locally over stdio. The bridge runs
here instead:

```
Slack ──(Socket Mode, outbound WebSocket)──> bin/slack-bridge.sh
        └─> claude -p  (only the omarchy-hardware server, only the tier's tools)
        <── reply in the thread
```

Socket Mode means no public URL, no inbound port and no tunnel. The bridge
opens one outbound TLS connection to Slack.

## What people in Slack can and cannot do

Nobody is allowed until you list them. There are two tiers:

| Tier | Can | Cannot |
|---|---|---|
| `read` | Every read-only tool: list and describe boards, profiles, history, parts, wiring checks, I2C identification, crash decoding, Pi/Jetson status, stored MING data | Open ports or change anything |
| `observe` | `read`, plus open/read/close serial ports, `serial_expect`, `fingerprint_board`, `compile_sketch`, `mpy_list` | Write to a port, flash, restore, drive GPIO, publish MQTT, run MicroPython code, label boards |

Opening a serial port resets many boards, which is why `observe` is its own tier.

**Nothing that writes, flashes, publishes or actuates is reachable from Slack.**
If someone asks for it, Claude tells them to ask you. You make that change in a
local session, where you confirm each step.

Every request runs a new headless Claude with these fences:
- `--restricted --tools ""`: no shell, no file access, no web;
- `--strict-mcp-config`: only the omarchy-hardware server, even if you have
  others configured;
- `--allowedTools` for the tier and `--disallowedTools` for every other tool;
- `--permission-mode dontAsk --permission-prompts none`: anything outside the
  allow list is denied, not asked;
- `--no-session-persistence`, `--max-budget-usd`, and a time limit;
- the Slack text goes in on stdin, inside `<slack_request>` tags. The system
  prompt says the request cannot change the rules. Replies are escaped so they
  can't ping `@channel` or anyone else.

Each request is written to the audit log (`slack_request`: who, where, tier,
outcome, and a SHA-256 of the text, not the text itself).

## Set it up

1. **Create the Slack app.** At api.slack.com/apps choose *Create New App → From a
   manifest*, pick the workspace, and paste
   [`examples/slack/manifest.yaml`](../examples/slack/manifest.yaml).
2. **Make an app-level token.** Under *Basic Information → App-Level Tokens*,
   generate one with the `connections:write` scope. It starts `xapp-`.
3. **Install the app** to the workspace and copy the *Bot User OAuth Token*
   (`xoxb-`).
4. **Store both tokens privately:**

   ```bash
   install -m 600 /dev/null ~/.config/omarchy-hardware/slack-app-token
   install -m 600 /dev/null ~/.config/omarchy-hardware/slack-bot-token
   $EDITOR ~/.config/omarchy-hardware/slack-app-token   # paste xapp-…
   $EDITOR ~/.config/omarchy-hardware/slack-bot-token   # paste xoxb-…
   ```

5. **Find the IDs.** A channel's ID is under *channel name → About* (`C…`). A
   person's is under *profile → ⋮ → Copy member ID* (`U…`).
6. **Add `[slack]` to `~/.config/omarchy-hardware/config.toml`:**

   ```toml
   [slack]
   enabled = true
   app_token_file = "~/.config/omarchy-hardware/slack-app-token"
   bot_token_file = "~/.config/omarchy-hardware/slack-bot-token"
   channels = ["C0123ABCD"]          # where it answers
   readers = ["U0123ABCD"]           # read tier
   observers = ["U0456EFGH"]         # observe tier (you, probably)
   # max_requests_per_hour = 20      # per person
   # max_runtime_s = 180
   # max_budget_usd = 1.0            # per request
   # model = "claude-sonnet-5"
   ```

   `*_env` works instead of `*_file`. The token files must be yours and mode
   `600`.
7. **Invite the app** to the channel with `/invite @omarchy-hardware`.
8. **Check, then run:**

   ```bash
   ~/.config/omarchy/plugins/io.github.sergiudanstan.hardware/bin/slack-bridge.sh --check
   ~/.config/omarchy/plugins/io.github.sergiudanstan.hardware/bin/slack-bridge.sh
   ```

   To start it at login, see
   [`examples/slack/omarchy-hardware-slack.service`](../examples/slack/omarchy-hardware-slack.service).
   The plugin installs no service itself.

## Costs and limits

Each request is one headless Claude run, billed to the account Claude Code is
signed in with. A simple question costs a few cents; a test "what's plugged in?"
cost $0.03. The limits are:
- `max_budget_usd` per request;
- `max_requests_per_hour` per person;
- one request at a time, with at most 5 waiting, since serial ports can't be
  shared.

The footer of each reply shows its tier, time and cost.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `--check` says the bridge is off | `[slack] enabled = true` is missing |
| `readable by other users` | `chmod 600` the token file |
| No answer, nothing in the log | The channel isn't in `channels`, or the app isn't invited there |
| "not on this workstation's Slack access list" | Add the person's `U…` ID to `readers` or `observers` |
| `connect failed` | Wrong app-level token, or Socket Mode is off for the app |
| "Claude Code was not found" | Set `OMARCHY_HARDWARE_CLAUDE` to Claude's absolute path |
