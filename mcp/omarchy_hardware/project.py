"""Start a Claude Code session for a board that was just plugged in.

`python -m omarchy_hardware.project /dev/ttyACM0` creates a project folder under
~/Projects/hw (OMARCHY_HARDWARE_PROJECTS overrides it) and fills it with:

- board.json: the board, its profile, its journal and the parts inventory;
- CLAUDE.md: how to work on hardware here, and a summary of board.json;
- .claude/commands/hw-*.md and .claude/skills/hardware-project/: the workflow;
- .mcp.json: only when the omarchy-hardware server is not registered already.

It then opens a terminal in that folder running Claude with a first prompt. The
session is interactive: Claude proposes, the user decides, and every flash still
needs confirm=true.

Everything the device reports (product strings, serial output) is data. It is
cleaned before it goes into CLAUDE.md, and CLAUDE.md says it is not instructions.
Nothing here writes outside the new folder, and no global Claude setting is
changed.

Stdlib-only, like boards.py, because the bar widget runs it before setup.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from string import Template
from typing import Any

from . import board_profiles, journal, parts
from .boards import enumerate_boards
from .config import ConfigError
from .config import load as load_config
from .errors import ToolError

PORT_PATTERN = re.compile(r"/dev/tty(?:ACM|USB)\d{1,3}")
PLUGIN_DIR = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
SERVER_NAME = "omarchy-hardware"
SEED_PROMPT = "A board was just plugged in. Read CLAUDE.md and board.json, then run /hw-propose."

UWSM_APP = "/usr/bin/uwsm-app"
TERMINAL_EXEC = "/usr/bin/xdg-terminal-exec"
CLAUDE_CANDIDATES = ("~/.local/bin/claude", "/usr/local/bin/claude", "/usr/bin/claude")


class LaunchError(RuntimeError):
    """Something the user must fix before a session can start; the message says what."""


def check_port(port: str) -> str:
    if not isinstance(port, str) or not PORT_PATTERN.fullmatch(port):
        raise LaunchError(f"{port!r} is not a /dev/ttyACM* or /dev/ttyUSB* port.")
    return port


def plain(text: object, limit: int = 80) -> str:
    """Device strings for a Markdown file: one line, printable, no fences or markup."""
    cleaned = "".join(ch if ch.isprintable() else " " for ch in str(text or ""))
    cleaned = re.sub(r"[`<>\[\]\\|#]", "'", cleaned)
    cleaned = " ".join(cleaned.split())
    return cleaned[:limit] or "unknown"


def projects_root() -> Path:
    configured = os.environ.get("OMARCHY_HARDWARE_PROJECTS")
    if configured:
        root = Path(configured)
        if not root.is_absolute():
            raise LaunchError("OMARCHY_HARDWARE_PROJECTS must be an absolute path.")
        return root
    return Path.home() / "Projects" / "hw"


def claude_path() -> str:
    configured = os.environ.get("OMARCHY_HARDWARE_CLAUDE")
    if configured:
        if not os.path.isabs(configured):
            raise LaunchError("OMARCHY_HARDWARE_CLAUDE must be an absolute path.")
        candidates: tuple[str, ...] = (configured,)
    else:
        candidates = tuple(os.path.expanduser(path) for path in CLAUDE_CANDIDATES)
    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    raise LaunchError(
        "Claude Code was not found at " + ", ".join(candidates) + ". Set OMARCHY_HARDWARE_CLAUDE to its absolute path."
    )


def _board(port: str) -> dict[str, Any]:
    for board in enumerate_boards():
        if board["port"] == port:
            return board
    raise LaunchError(f"No board is connected on {port}.")


def _flash_status(project_dir: Path) -> dict[str, Any]:
    try:
        config = load_config()
    except ConfigError as exc:
        return {"allowed": False, "config_error": str(exc)}
    roots = [os.path.realpath(root) for root in config.sketch_roots]
    target = os.path.realpath(project_dir)
    inside = any(target == root or target.startswith(root.rstrip("/") + "/") for root in roots)
    return {"allowed": config.allow_flash, "project_inside_sketch_roots": inside, "sketch_roots": roots}


def gather(port: str, project_dir: Path) -> dict[str, Any]:
    """Everything the session starts from. The USB serial number is left out."""
    board = _board(check_port(port))
    fqbn = board.get("suggested_fqbn")
    profile_id = board_profiles.profile_id_for_fqbn(fqbn)
    try:
        history = journal.history(board)
    except ToolError as exc:
        history = {"tracked": False, "reason": exc.message}
    try:
        inventory: dict[str, Any] = parts.load()
    except ToolError as exc:
        inventory = {"configured": False, "parts": [], "error": exc.message}
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "port": port,
        "board": {
            key: board.get(key)
            for key in ("friendly_name", "product", "manufacturer", "vid", "pid", "board_type",
                        "suggested_fqbn", "suggested_baud")
        },
        "profile": board_profiles.export(profile_id) if profile_id else None,
        "history": history,
        "parts": inventory,
        "flash": _flash_status(project_dir),
    }


def _slug(context: dict[str, Any]) -> str:
    history = context["history"]
    base = history.get("label") or (context["profile"] or {}).get("id") or context["board"].get("board_type") or "board"
    slug = re.sub(r"[^a-z0-9]+", "-", str(base).lower()).strip("-")[:40]
    if not slug or slug == "unknown":
        slug = "board"
    return slug


def _flash_line(flash: dict[str, Any], project_dir: Path) -> str:
    if "config_error" in flash:
        error = plain(flash["config_error"], 160)
        return f"config.toml has an error ({error}); flashing will be refused until it is fixed."
    if not flash["allowed"]:
        return "Flashing is off. The user enables it with `[flash] allow = true` and `sketch_roots` in config.toml."
    if not flash["project_inside_sketch_roots"]:
        return (
            f"This folder ({project_dir}) is not inside `[flash] sketch_roots`, so upload_sketch will refuse it. "
            "Ask the user to add the projects folder to sketch_roots; do not edit config.toml yourself."
        )
    return "Flashing is enabled for this folder. Every upload still needs the user's go-ahead and confirm=true."


def _history_line(history: dict[str, Any]) -> str:
    if not history.get("tracked"):
        return "Not tracked (no USB serial number)."
    if not history.get("known"):
        return "First time this board has been seen."
    last = history.get("last_upload")
    label = f"Labelled {plain(history['label'], 40)}. " if history.get("label") else ""
    if not last:
        return label + "No uploads recorded."
    return (
        f"{label}{history['upload_count']} upload(s) recorded; the last was {plain(last['sketch'], 60)} "
        f"({plain(last['fqbn'], 80)}) at {plain(last['at'], 40)}, from {plain(last['sketch_dir'], 160)}."
    )


def render_claude_md(context: dict[str, Any], project_dir: Path) -> str:
    board = context["board"]
    profile = context["profile"]
    inventory = context["parts"]
    if profile:
        profile_line = (
            f"`{profile['id']}` ({profile['name']}): {profile['logic_voltage']} V logic, "
            f"5 V tolerant: {profile['five_volt_tolerant']}. Call `board_profile(port=\"{context['port']}\")` for pins."
        )
    else:
        profile_line = (
            f"None matched. Try `fingerprint_board(port=\"{context['port']}\")` (it resets the board, so ask "
            "first), or ask the user which board this is, then call `board_profile(fqbn=...)`. "
            "If there is still no profile, say that pin facts are unverified."
        )
    if inventory.get("error"):
        parts_line = f"parts.toml has an error: {plain(inventory['error'], 160)}"
    elif inventory.get("configured"):
        parts_line = f"{len(inventory['parts'])} part(s) listed. Call `parts_inventory()`."
    else:
        parts_line = "No parts.toml yet. Ask the user what they have on the bench."
    template = Template((TEMPLATE_DIR / "CLAUDE.md").read_text(encoding="utf-8"))
    return template.substitute(
        board_name=plain(board.get("friendly_name")),
        port=context["port"],
        vid_pid=f"{plain(board.get('vid'), 4)}:{plain(board.get('pid'), 4)}",
        fqbn=plain(board.get("suggested_fqbn") or "not identified", 120),
        baud=int(board.get("suggested_baud") or 115200),
        profile_line=profile_line,
        history_line=_history_line(context["history"]),
        parts_line=parts_line,
        flash_line=_flash_line(context["flash"], project_dir),
    )


def _server_registered() -> bool:
    try:
        with open(Path.home() / ".claude.json", encoding="utf-8") as handle:
            settings = json.load(handle)
    except (OSError, ValueError):
        return False
    servers = settings.get("mcpServers") if isinstance(settings, dict) else None
    return isinstance(servers, dict) and SERVER_NAME in servers


def create(port: str) -> Path:
    root = projects_root()
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    # gather() needs the final path for the sketch_roots check; the slug needs gather().
    provisional = root / f"board-{stamp}"
    context = gather(port, provisional)
    name = f"{_slug(context)}-{stamp}"
    # Two sessions started in the same second (a double click) get separate folders.
    for attempt in range(1, 100):
        project_dir = root / (name if attempt == 1 else f"{name}-{attempt}")
        try:
            project_dir.mkdir()
            break
        except FileExistsError:
            continue
    else:
        raise LaunchError(f"Could not create a new folder in {root}.")
    context["flash"] = _flash_status(project_dir)

    (project_dir / "board.json").write_text(json.dumps(context, indent=2) + "\n", encoding="utf-8")
    (project_dir / "CLAUDE.md").write_text(render_claude_md(context, project_dir), encoding="utf-8")
    shutil.copytree(TEMPLATE_DIR / "claude", project_dir / ".claude")
    if not _server_registered():
        mcp = {"mcpServers": {SERVER_NAME: {"type": "stdio", "command": str(PLUGIN_DIR / "bin" / "hardware-mcp")}}}
        (project_dir / ".mcp.json").write_text(json.dumps(mcp, indent=2) + "\n", encoding="utf-8")
    return project_dir


def terminal_argv(project_dir: Path, command: list[str]) -> list[str]:
    launcher = [UWSM_APP, "--"] if os.access(UWSM_APP, os.X_OK) else []
    return [*launcher, TERMINAL_EXEC, f"--dir={project_dir}", *command]


def child_env() -> dict[str, str]:
    """The bin wrapper pins PATH for itself; the user's own session gets theirs back."""
    env = dict(os.environ)
    user_path = env.pop("OMARCHY_HARDWARE_USER_PATH", None)
    if user_path:
        env["PATH"] = user_path
    return env


def spawn(argv: list[str], cwd: Path | None = None) -> None:
    # S603: argv list, shell=False; every element is a fixed path, a validated port,
    # or a folder this module just created.
    subprocess.Popen(  # noqa: S603
        argv, cwd=cwd, env=child_env(), start_new_session=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def launch(port: str) -> Path:
    claude = claude_path()
    project_dir = create(check_port(port))
    spawn(terminal_argv(project_dir, [claude, SEED_PROMPT]), cwd=project_dir)
    return project_dir


def notify_error(message: str) -> None:
    subprocess.run(  # noqa: S603
        ["/usr/bin/notify-send", "--app-name=Hardware", "Could not start a hardware session", message],
        check=False, timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m omarchy_hardware.project /dev/ttyACM0", file=sys.stderr)
        return 2
    try:
        print(launch(args[0]))
    except (LaunchError, ToolError, OSError) as exc:
        message = exc.message if isinstance(exc, ToolError) else str(exc)
        print(message, file=sys.stderr)
        notify_error(message)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
