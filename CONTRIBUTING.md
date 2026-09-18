# Contributing

Contributions are welcome. This is a single-maintainer project, so please open an issue before
starting anything substantial — it avoids wasted effort if the idea does not fit.

**Security problems do not belong here.** Follow [SECURITY.md](SECURITY.md) and report them
privately instead of opening an issue or PR.

## Several agents on this repository

Agents (Grok, Codex, Copilot, Claude, …) must use **git + `gh`**, not GitHub Desktop.
GitHub Desktop is optional for a human reviewing the same clone.

- One branch per change-set: `agent/<name>/<topic>`, never force-push `main`.
- Open a pull request with `gh pr create` and wait for CI. Merge with `gh pr merge`
  only when CI is green and the change is in scope.
- Record the work in `REMEDIATION-PLAN.md` (collaboration log) in the same commit
  as the code. Do not leave `TBD` for a commit that already exists.
- Do not invent hardware results. Physical flashing and Pi GPIO stay in
  `docs/hardware-validation.md` and stay empty until someone actually ran them.
- `gh` must be on `PATH` and authenticated as `sergiudanstan`. Its location differs
  between the maintainer's machines, so find it with `command -v gh` instead of
  assuming a path.

## Branches, tags and releases

There is one long-lived branch, `main`. Everything else is short-lived:

- **Work branches.** Agents use `agent/<name>/<topic>`, humans `<type>/<topic>`
  (`feat/`, `fix/`, `docs/`, `release/`). One change-set per branch, merged into
  `main` through a pull request with the required checks green. GitHub deletes the
  branch on merge; nothing else should outlive its PR.
- **Merges.** Merge commits, not squash or rebase, so every PR stays visible as one
  unit in `git log --first-parent main`. Commits must be signed (branch protection).
- **Releases.** A release is a PR that bumps `manifest.json`, `mcp/pyproject.toml`
  and `omarchy_hardware/__init__.py` together and moves `[Unreleased]` in the
  changelog to a dated version. After it merges, a signed `vX.Y.Z` tag on that merge
  commit triggers `release.yml` (artifacts, SBOM, attestations). Tags are never
  moved or deleted.
- **Marketplace reviews.** A verification request names an exact commit. Mark it with a
  signed tag, `marketplace-review-<version>`, when the request is opened, and keep that tag
  while the request is open. `main` keeps moving; the tag is what stays fixed for the
  reviewer. Open the next verification only after the current one closes.

## Getting set up

```bash
git clone https://github.com/sergiudanstan/omarchy-hardware
cd omarchy-hardware
python3 -m venv .dev-venv
./.dev-venv/bin/pip install -e './mcp[dev]'
./.dev-venv/bin/python -m pytest mcp/tests -q
```

To run it as a real plugin, clone into `~/.config/omarchy/plugins/io.github.sergiudanstan.hardware/`
and run `bin/setup.sh`. Saving a file under that directory reloads the plugin automatically; a
newly *enabled* widget needs `omarchy restart shell` before it appears.

> `omarchy refresh shell` **resets `shell.json` to Omarchy defaults**. It is not a reload. Use
> `omarchy restart shell`.

## Before you open a pull request

Everything below runs in CI, so checking locally saves a round trip:

```bash
./.dev-venv/bin/python -m pytest mcp/tests -q
./.dev-venv/bin/ruff check mcp/omarchy_hardware mcp/tests
shellcheck --severity=style bin/setup.sh bin/doctor.sh bin/scan-boards.sh bin/hardware-mcp bin/test-native.sh
python3 .github/scripts/check_manifest.py
python3 .github/scripts/check_versions.py
omarchy plugin validate .    # only on an Omarchy machine
```

## Things that will get a PR rejected

These are not style preferences — they are the plugin's security boundaries:

- **Introducing a tool that runs arbitrary commands on a remote host.** GPIO works by building
  fixed `pinctrl`/`raspi-gpio` argv lists. Do not add a general "run this on the Pi" tool.
- **Using `shell=True`,** or building a command by string interpolation of any value that came
  from a tool argument.
- **Widening the serial device allowlist** without re-validating after `realpath`. The
  post-resolution check is what stops a swapped device node redirecting a write.
- **Weakening the flash gate.** Uploading firmware requires both a compile-minted token and an
  explicit `confirm`.
- **Adding a symlink anywhere in the repository.** The Omarchy plugin validator rejects them
  outright, which is also why the virtualenv lives outside the plugin directory.
- **Blocking calls in QML.** Everything the widget does must go through an async `Process`; a
  synchronous call freezes the entire desktop shell.
- **Silently suppressing a `ruff` security finding.** If a rule is a false positive, add a
  `# noqa` *with a comment explaining why it is safe here*.

## Conventions

- Tests must not require physical hardware. The serial layer is tested against `pty` pairs;
  follow that pattern.
- Changes affecting behaviour need a `CHANGELOG.md` entry.
- Version bumps must update all three of `manifest.json`, `mcp/pyproject.toml` and
  `mcp/omarchy_hardware/__init__.py`. CI fails if they disagree.
- Comments should explain *why*, not restate the code.

## Licence

Contributions are accepted under the [MIT Licence](LICENSE).
