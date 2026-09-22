"""The NIS2 runner and its mapping document must list the same checks."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECK_ID = re.compile(r"NIS2-[A-J]-\d+")


def test_every_nis2_check_is_mapped_and_every_mapping_is_a_check():
    runner = set(re.findall(r'ev\.run\("(NIS2-[A-J]-\d+)"', (ROOT / "mcp/hardware_validation/run_nis2.py").read_text()))
    table = {
        match.group(0)
        for line in (ROOT / "docs/nis2.md").read_text().splitlines()
        if line.startswith("| NIS2-")
        for match in [CHECK_ID.search(line)]
    }
    assert runner, "no checks found in run_nis2.py"
    assert runner == table, {"unmapped": sorted(runner - table), "stale_rows": sorted(table - runner)}
