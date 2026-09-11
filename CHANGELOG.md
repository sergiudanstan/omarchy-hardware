# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.1] - 2026-09-11

### Fixed
- **The `pinctrl` output parser was broken for almost every real format.** It assumed at most
  one optional pull token, so `26: ip -- | lo`, `26: op -- -- | lo`, `6: op dl pu | lo` and
  `17: op dh | hi` were all silently skipped — only the `a3 pu` shape parsed. On a real Pi,
  `gpio_list_pins` would have returned little or nothing and `gpio_read_pin` would have failed
  outright. The parser now reads the whole flag run, and also reports drive state (`dh`/`dl`).

### Added
- Upload-token regression tests: the token is bound to the exact sketch, board and expiry it
  was minted for, and a forged signature or extended expiry is rejected.
- GPIO parser regression tests covering the real `pinctrl` and `raspi-gpio` output formats
  (52 tests total, none requiring hardware).

### Changed
- Documentation now distinguishes what is actually verified. `compile_sketch` is verified end
  to end through the MCP server, producing a real `.hex`; only the final `upload_sketch` write
  to a board and Pi GPIO remain unverified against physical hardware.

## [0.1.0] - 2026-09-10

First release.

### Added

**The plugin**
- Quickshell bar widget listing connected USB development boards, with a panel showing
  each board's port, identity and accessibility, and a banner reporting outstanding setup.
- MCP server exposing 18 tools to Claude Code.
- Board discovery via sysfs with VID/PID identification for Arduino, ESP32, RP2040 and
  common USB-serial bridge chips.
- Serial sessions owned by the server process and drained by a background thread into a
  bounded ring buffer, so a read never blocks indefinitely on a silent device.
- Sketch compiling and flashing through `arduino-cli`, gated by a compile-minted HMAC
  token plus explicit confirmation.
- Raspberry Pi GPIO over SSH using fixed `pinctrl`/`raspi-gpio` argv against host and pin
  allowlists. No tool runs arbitrary commands on the Pi.
- `bin/setup.sh`: one-time, idempotent, user-run setup with a `--check` mode.

**Assurance**
- 26 tests requiring no hardware, using PTY pairs, including adversarial path-escape cases.
- CI on every push: the suite on Python 3.11–3.13 (3.14 non-blocking), `shellcheck`,
  manifest validation mirroring Omarchy's own validator, a symlink guard, and a check that
  the three version strings agree.
- Security tooling: `ruff` with the flake8-bandit ruleset, `pip-audit` against the
  lockfile, and `zizmor` auditing the workflows. CodeQL and OpenSSF Scorecard weekly.
- `mcp/requirements.lock`: 29 packages pinned with hashes, covering all transitives.
- GitHub Actions pinned to full commit SHAs; workflow tokens default to `contents: read`
  and no checkout persists credentials.
- `SECURITY.md` with a private reporting channel, response targets and explicit scope;
  `docs/threat-model.md`; `CONTRIBUTING.md`.
- Release artifacts carry build provenance and a CycloneDX SBOM.

### Known limitations
- Flashing and Raspberry Pi GPIO are implemented and guarded but have **not** been verified
  against physical hardware.
- QML is not statically linted in CI: `qmllint` needs Qt plus Quickshell type registrations
  that hosted runners do not provide. `BoardsModel.js` is syntax-checked instead.
- Single maintainer, so OpenSSF Scorecard's Code-Review and Contributors checks cannot pass.

[Unreleased]: https://github.com/sergiudanstan/omarchy-hardware/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/sergiudanstan/omarchy-hardware/releases/tag/v0.1.1
[0.1.0]: https://github.com/sergiudanstan/omarchy-hardware/releases/tag/v0.1.0
