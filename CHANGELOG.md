# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Continuous integration: the test suite now runs on every push across Python 3.11–3.13
  (3.14 non-blocking), alongside `shellcheck`, manifest validation, and a check that the
  three version strings agree.
- Security tooling in CI: `ruff` including the flake8-bandit ruleset, `pip-audit` for known
  dependency vulnerabilities, and `zizmor` auditing the workflows themselves.
- `SECURITY.md` with a private vulnerability-reporting channel, response targets, and an
  explicit in-scope/out-of-scope list.
- `docs/threat-model.md` documenting trust boundaries, controls, and accepted residual risks.
- `CONTRIBUTING.md`, including the security boundaries a pull request must not weaken.
- `mcp/requirements.lock`, hash-pinned, so installs are reproducible.
- Dependabot configuration for Python dependencies and GitHub Actions.

### Changed
- GitHub Actions are pinned to full commit SHAs rather than mutable tags.
- `bin/setup.sh` installs dependencies from the hash-pinned lockfile before installing the
  package itself.

### Fixed
- A failure to close a serial port is now recorded on the session instead of being silently
  swallowed, so a later `PORT_BUSY` can be explained.

## [0.1.0] - 2026-09-10

### Added
- Initial release: Quickshell bar widget listing connected USB development boards, and an MCP
  server exposing 18 tools to Claude Code.
- Board discovery via sysfs with VID/PID identification for Arduino, ESP32, RP2040 and common
  USB-serial bridge chips.
- Serial sessions owned by the server process, drained by a background thread into a bounded
  ring buffer so reads never block indefinitely.
- Sketch compiling and flashing via `arduino-cli`, gated by a compile-minted HMAC token plus
  explicit confirmation.
- Raspberry Pi GPIO over SSH using fixed `pinctrl`/`raspi-gpio` argv against host and pin
  allowlists.
- `bin/setup.sh` for one-time, idempotent, user-run setup.

### Known limitations
- Flashing and Raspberry Pi GPIO are implemented but have not been verified against physical
  hardware.
- QML is not statically linted in CI; `qmllint` requires Qt and Quickshell type registrations
  that are unavailable on hosted runners.

[Unreleased]: https://github.com/sergiudanstan/omarchy-hardware/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/sergiudanstan/omarchy-hardware/releases/tag/v0.1.0
