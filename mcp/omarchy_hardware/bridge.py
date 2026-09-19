"""Forward a board's JSON lines from an open serial session to an MQTT topic.

This is how a sketch built with /hw-build reaches the MING stack: it prints one
JSON object per reading, the bridge publishes each to an allowlisted topic, and
the stack (for example Node-RED or Telegraf) stores it in InfluxDB for Grafana.

Limits, because the bridge publishes on the user's behalf with no call per message:

- Only lines that parse as a JSON object are forwarded, re-serialised, so the
  payload is always well-formed JSON and never raw device bytes.
- At most one message per min_interval_ms. Faster lines are dropped (counted),
  and the newest pending one is sent when the interval ends.
- Every publish is charged to the MING write budget for that topic. When the
  budget is spent, messages are dropped, not queued.
- A bridge stops by itself after duration_s (at most an hour), when its session
  closes, or on serial_bridge_stop. It is audited when it starts and when it stops,
  with the counts, not per message.

While a bridge runs it owns the session's input. Its status keeps the last few
lines, so the readings stay visible.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from typing import Any

from . import errors
from .errors import ToolError

MAX_DURATION_S = 3600
MIN_INTERVAL_MS = 200
MAX_BRIDGES = 4
TAIL_LINES = 5
POLL_MS = 250


def _json_object(line: str) -> dict[str, Any] | None:
    text = line.strip()
    if not text.startswith("{") or len(text) > 4096:
        return None
    try:
        value = json.loads(text)
    except (ValueError, RecursionError):
        return None
    return {"json": value} if isinstance(value, dict) else None


class Bridge:
    def __init__(
        self,
        session: Any,
        topic: str,
        broker: str,
        publish: Callable[[bytes], None],
        charge: Callable[[], None],
        *,
        duration_s: int,
        min_interval_ms: int,
        max_payload: int,
        on_stop: Callable[[Bridge], None],
    ) -> None:
        self.bridge_id = uuid.uuid4().hex[:10]
        self.session = session
        self.topic = topic
        self.broker = broker
        self._publish = publish
        self._charge = charge
        self.min_interval = min_interval_ms / 1000.0
        self.max_payload = max_payload
        self.deadline = time.monotonic() + duration_s
        self.started_at = time.time()
        self.published = 0
        self.dropped_rate = 0
        self.dropped_budget = 0
        self.dropped_size = 0
        self.errors: list[str] = []
        self.tail: deque[str] = deque(maxlen=TAIL_LINES)
        self.stop_reason: str | None = None
        self._stop = threading.Event()
        self._on_stop = on_stop
        self._pending: bytes | None = None
        self._last_sent = 0.0
        self._thread = threading.Thread(target=self._run, name=f"bridge-{self.bridge_id}", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self, reason: str = "stopped") -> None:
        if self.stop_reason is None:
            self.stop_reason = reason
        self._stop.set()

    def join(self, timeout: float = 5.0) -> None:
        self._thread.join(timeout)

    @property
    def running(self) -> bool:
        return self._thread.is_alive() and not self._stop.is_set()

    def _send(self, payload: bytes) -> None:
        try:
            self._charge()
        except ToolError:
            self.dropped_budget += 1
            return
        try:
            self._publish(payload)
            self.published += 1
        except ToolError as exc:
            self.errors = [*self.errors[-4:], exc.message]
        self._last_sent = time.monotonic()

    def _offer(self, reading: dict[str, Any]) -> None:
        payload = json.dumps(reading, separators=(",", ":")).encode()
        if len(payload) > self.max_payload:
            self.dropped_size += 1
            return
        if time.monotonic() - self._last_sent >= self.min_interval:
            self._send(payload)
        else:
            if self._pending is not None:
                self.dropped_rate += 1
            self._pending = payload

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                if time.monotonic() >= self.deadline:
                    self.stop("duration reached")
                    break
                if self._pending is not None and time.monotonic() - self._last_sent >= self.min_interval:
                    pending, self._pending = self._pending, None
                    self._send(pending)
                try:
                    result = self.session.expect(_json_object, POLL_MS)
                except ToolError:
                    self.stop("serial session closed")
                    break
                for line in result.get("context", []):
                    self.tail.append(line)
                if result.get("matched"):
                    self.tail.append(result["line"][:200])
                    self._offer(result["match"]["json"])
        finally:
            self._on_stop(self)

    def status(self) -> dict[str, Any]:
        return {
            "bridge_id": self.bridge_id,
            "port": self.session.port,
            "broker": self.broker,
            "topic": self.topic,
            "running": self.running,
            "stop_reason": self.stop_reason,
            "published": self.published,
            "dropped": {"rate": self.dropped_rate, "budget": self.dropped_budget, "size": self.dropped_size},
            "errors": list(self.errors),
            "seconds_left": max(0, round(self.deadline - time.monotonic())) if self.running else 0,
            "last_lines": list(self.tail),
            "untrusted": True,
        }


class Registry:
    def __init__(self) -> None:
        self._bridges: dict[str, Bridge] = {}
        self._lock = threading.Lock()

    def add(self, bridge: Bridge) -> None:
        with self._lock:
            active = [b for b in self._bridges.values() if b.running]
            if any(b.session.port == bridge.session.port for b in active):
                raise ToolError(errors.PORT_BUSY, f"{bridge.session.port} is already bridged.",
                                "Stop that bridge first with serial_bridge_stop.")
            if len(active) >= MAX_BRIDGES:
                raise ToolError(errors.RATE_LIMITED, f"At most {MAX_BRIDGES} bridges run at once.")
            # Forget finished bridges beyond the active ones so status stays short.
            self._bridges = {key: b for key, b in self._bridges.items() if b.running}
            self._bridges[bridge.bridge_id] = bridge
        bridge.start()

    def get(self, bridge_id: str) -> Bridge:
        with self._lock:
            bridge = self._bridges.get(bridge_id)
        if bridge is None:
            raise ToolError(errors.SESSION_NOT_FOUND, f"No bridge {bridge_id!r}.", "Call serial_bridge_status.")
        return bridge

    def by_port(self, port: str) -> Bridge | None:
        with self._lock:
            return next((b for b in self._bridges.values() if b.running and b.session.port == port), None)

    def all(self) -> list[dict[str, Any]]:
        with self._lock:
            bridges = list(self._bridges.values())
        return [bridge.status() for bridge in bridges]
