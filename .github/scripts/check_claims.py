#!/usr/bin/env python3
"""Verify the countable claims the security documentation makes about itself.

The threat model is the evidence a regulated adopter is pointed at (SECURITY.md,
"What this project does not claim"). It has drifted from the repository twice --
a stale test count, and a required status check that was never required -- and a
reviewer who spot-checks three claims and finds one wrong discounts the other
thirty. So the claims that *can* be checked mechanically are checked here, on
every push, instead of being re-verified by hand and eventually not at all.

Claims needing repository settings (branch protection, required checks) cannot be
read with the default workflow token and stay in docs/threat-model.md under
"Assurance", where they are reviewed when protection changes.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
THREAT_MODEL = ROOT / "docs" / "threat-model.md"
TEST_COUNT = re.compile(r"(?P<count>\d+)\s+automated tests")


def collected_tests() -> int:
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", "mcp/tests", "--collect-only", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        sys.exit(f"could not collect tests:\n{result.stdout}\n{result.stderr}")

    match = re.search(r"(\d+) tests? collected", result.stdout)
    if not match:
        sys.exit(f"could not read a test count from pytest output:\n{result.stdout[-2000:]}")
    return int(match.group(1))


def main() -> int:
    text = THREAT_MODEL.read_text(encoding="utf-8")
    claim = TEST_COUNT.search(text)
    if claim is None:
        sys.exit(f"{THREAT_MODEL.relative_to(ROOT)} no longer states a test count; update this check with it")

    claimed = int(claim.group("count"))
    actual = collected_tests()

    if claimed != actual:
        print(
            f"docs/threat-model.md claims {claimed} automated tests; the suite collects {actual}.\n"
            f"Update the claim, because it is offered to third parties as evidence.",
            file=sys.stderr,
        )
        return 1

    print(f"threat model claims {claimed} automated tests; pytest collects {actual}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
