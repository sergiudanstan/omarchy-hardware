"""Read-only, stdlib-only CLI. Discovery reads sysfs; no command opens a port."""

from __future__ import annotations

import argparse
import json
import sys
import tomllib

from . import board_profiles, boards, displays, errors, reference
from .config import ConfigError, load
from .errors import ToolError, ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("boards")
    commands.add_parser("profiles")
    commands.add_parser("profile").add_argument("id")
    ref = commands.add_parser("reference")
    ref.add_argument("--device")
    ref.add_argument("--markdown", action="store_true")
    commands.add_parser("displays").add_argument("controller", nargs="?")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    try:
        if args.command == "boards":
            payload = ok(boards=boards.with_labels(boards.all_boards()))
        elif args.command == "profiles":
            payload = ok(profiles=board_profiles.index())
        elif args.command == "profile":
            payload = ok(profile=board_profiles.export(args.id))
        elif args.command == "displays":
            payload = ok(display=displays.lookup(args.controller)) if args.controller else ok(**displays.export())
        else:
            config = load()
            result = (reference.device_reference(args.device, config) if args.device
                      else reference.export_reference(config))
            if args.markdown:
                print(reference.render_markdown(result), end="")
                return 0
            payload = ok(reference=result)
    except ToolError as exc:
        payload = exc.as_result()
    except (ConfigError, OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        payload = ToolError(errors.CONFIG_ERROR, str(exc)).as_result()
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if payload["ok"] else 1
