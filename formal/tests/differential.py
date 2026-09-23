"""Differential test: the Lean models against the Python they model.

The Lean theorems are about the definitions in formal/OmarchyFormal. They say something
about the plugin only if those definitions compute what the Python computes, so this
feeds the same random inputs to both — the real functions imported from
mcp/omarchy_hardware, and the compiled Lean oracle — and fails on the first disagreement.

    cd formal && lake build && python3 tests/differential.py [--cases N] [--seed S]
"""

from __future__ import annotations

import argparse
import random
import re
import subprocess
import sys
from pathlib import Path
from unittest import mock

FORMAL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FORMAL.parent / "mcp"))

from omarchy_hardware import config, mqtt_lite, policy, weintek  # noqa: E402
from omarchy_hardware.errors import ToolError  # noqa: E402

ORACLE = FORMAL / ".lake" / "build" / "bin" / "oracle"

# Levels chosen to hit every branch: wildcards, '$' levels, wildcard-looking literals,
# empty levels, and non-ASCII.
LEVELS = ["a", "b", "+", "#", "$SYS", "$x", "+a", "#b", "", "é", "a+", "plant", "line1"]


def topic(rng: random.Random) -> str:
    return "/".join(rng.choice(LEVELS) for _ in range(rng.randint(1, 5)))


def read_packet_length(lengths: list[int]) -> str:
    """Decode a remaining length with the real `Client._read_packet`.

    Only the socket read is stubbed. `_max_packet = -1` makes the real code report every
    length it decodes in its "packet too large" error, so no framing logic is duplicated
    here; running out of bytes is the stub's TimeoutError, as on a silent socket.
    """
    client = object.__new__(mqtt_lite.Client)
    client._max_packet = -1
    data = bytes([0x30, *lengths])
    pos = 0

    def recv_exact(count: int, deadline: float) -> bytes:
        nonlocal pos
        if pos + count > len(data):
            raise TimeoutError
        pos += count
        return data[pos - count : pos]

    client._recv_exact = recv_exact  # type: ignore[method-assign]
    try:
        client._read_packet(0.0)
    except TimeoutError:
        return "none"
    except mqtt_lite.MqttError as exc:
        match = re.match(r"broker sent a (\d+)-byte packet", str(exc))
        return f"{match.group(1)} {len(data) - pos}" if match else "none"
    raise AssertionError("a negative packet limit must reject every length")


class Oracle:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.expected: list[tuple[str, str]] = []

    def add(self, request: str, python_answer: str) -> None:
        assert "\n" not in request
        self.lines.append(request)
        self.expected.append((request, python_answer))

    def check(self) -> int:
        out = subprocess.run(
            [str(ORACLE)], input="\n".join(self.lines) + "\n", capture_output=True, text=True, check=True
        ).stdout.splitlines()
        assert len(out) == len(self.expected), (len(out), len(self.expected))
        failures = [(req, py, lean) for (req, py), lean in zip(self.expected, out) if py != lean]
        for req, py, lean in failures[:20]:
            print(f"MISMATCH {req!r}: python={py!r} lean={lean!r}")
        return len(failures)


def bit(value: bool) -> str:
    return "1" if value else "0"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=20260923)
    args = parser.parse_args()
    rng = random.Random(args.seed)
    oracle = Oracle()
    counts: dict[str, int] = {}

    def add(kind: str, request: str, answer: str) -> None:
        counts[kind] = counts.get(kind, 0) + 1
        oracle.add(request, answer)

    # MQTT topic filters: topic_matches and filter_covers on the real functions.
    for _ in range(args.cases):
        f, t = topic(rng), topic(rng)
        add("topic_matches", f"matches\t{f}\t{t}", bit(mqtt_lite.topic_matches(f, t)))
        a, r = topic(rng), topic(rng)
        if rng.random() < 0.5:  # bias towards narrowings so True is well represented
            r = a.replace("#", rng.choice(["#", "a/#", "+/b", "a"]), 1).replace("+", rng.choice(["+", "a"]), 1)
        add("filter_covers", f"covers\t{a}\t{r}", bit(mqtt_lite.filter_covers(a, r)))

    # Remaining length: encoder on the boundary values and random ones.
    boundaries = [0, 1, 127, 128, 16383, 16384, 2097151, 2097152, 268435455]
    for n in boundaries + [rng.randint(0, mqtt_lite.MAX_REMAINING_LENGTH) for _ in range(args.cases)]:
        add("encode_length", f"enc\t{n}", " ".join(str(b) for b in mqtt_lite._encode_length(n)))

    # Remaining length: the real _read_packet over arbitrary byte strings, including
    # five-byte and truncated encodings and non-minimal ones.
    for _ in range(args.cases):
        raw = [rng.choice([0, 1, 127, 128, 129, 255, rng.randint(0, 255)]) for _ in range(rng.randint(0, 6))]
        add("read_packet", f"dec\t{' '.join(map(str, raw))}", read_packet_length(raw))

    # Packet ids: the real _packet_id.
    for n in [1, 2, 65534, 65535] + [rng.randint(1, 65535) for _ in range(200)]:
        client = object.__new__(mqtt_lite.Client)
        client._next_id = n
        client._packet_id()
        add("packet_id", f"pid\t{n}", str(client._next_id))

    # Modbus: _modbus_range acceptance, covers, check_weintek_modbus, modbus_address.
    areas = list(config.MODBUS_AREAS)
    for _ in range(args.cases):
        area = rng.choice(areas)
        size = config.MODBUS_AREAS[area]
        start = rng.choice([0, rng.randint(0, size), size - 1, size, rng.randint(0, 99999)])
        count = rng.choice([0, 1, rng.randint(1, 64), rng.randint(1, 9999), max(1, size - start)])
        try:
            config._modbus_range(f"{area}-{start}:{count}")
            valid = "1"
        except config.ConfigError:
            valid = "0"
        add("modbus_range", f"valid\t{area}:{start}:{count}", valid)

        allow = []
        for _ in range(rng.randint(1, 3)):
            a2 = rng.choice(areas)
            s2 = rng.randint(0, config.MODBUS_AREAS[a2] - 1)
            c2 = rng.randint(1, config.MODBUS_AREAS[a2] - s2)
            allow.append(config.ModbusRange(a2, s2, c2))
        pick = rng.choice(allow)
        q_area = rng.choice([pick.area, rng.choice(areas)])
        q_start = rng.choice([pick.start, pick.start + rng.randint(-3, pick.count + 3), rng.randint(0, 65535)])
        q_start = max(0, q_start)
        q_count = rng.randint(1, weintek.MAX_MODBUS_BITS if q_area == "LB" else weintek.MAX_MODBUS_WORDS)
        cfg = config.Config(
            weintek_allow=True,
            weintek_modbus=(config.WeintekModbusTarget("hmi", 502, 1, tuple(allow)),),
        )
        try:
            policy.check_weintek_modbus(cfg, "hmi", 502, q_area, q_start, q_count)
            authorised = "1"
        except ToolError:
            authorised = "0"
        fn, addr = weintek.modbus_address(q_area, q_start)
        spec = ",".join(f"{r.area}:{r.start}:{r.count}" for r in allow)
        add("modbus_read", f"modbus\t{spec}\t{q_area}\t{q_start}\t{q_count}", f"{authorised} {fn} {addr}")

    # Rolling budgets: the real RollingBudget with a controlled clock.
    for _ in range(args.cases // 5):
        limit = rng.randint(0, 10)
        window = rng.choice([1, 5, 60])
        t = 0
        events = []
        for _ in range(rng.randint(1, 25)):
            t += rng.choice([0, 0, 1, 2, window - 1, window, window + 1, rng.randint(0, 3 * window)])
            events.append((t, rng.randint(0, 4)))
        budget = policy.RollingBudget(limit)
        budget.window = float(window)
        decisions = ""
        for now, c in events:
            with mock.patch.object(policy.time, "monotonic", return_value=float(now)):
                try:
                    budget.charge("target", c)
                    decisions += "A"
                except ToolError:
                    decisions += "R"
        spec = ",".join(f"{now}:{c}" for now, c in events)
        add("rolling_budget", f"budget\t{limit}\t{window}\t{spec}", decisions)

    failures = oracle.check()
    for kind, n in counts.items():
        print(f"{kind:16} {n:6} cases")
    print(f"{'total':16} {sum(counts.values()):6} cases, {failures} mismatches")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
