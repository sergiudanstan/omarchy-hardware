"""Shared capability records returned by hardware adapters."""

from __future__ import annotations

from typing import TypedDict


class CapabilityOperation(TypedDict):
    id: str
    safety: str
    available: bool


class CapabilityDevice(TypedDict):
    family: str
    model: str | None
    identity: str
    capabilities: list[str]
    health: str
