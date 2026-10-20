"""Check whether relay handoffs preserve the charter's fixed objective."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


FIXED_OBJECTIVE = (
    "Build a reproducible objective-retention benchmark for a five-node relay, "
    "with distracting inputs, grounded checks, a negative control, and an honest "
    "results report."
)
_REQUIRED_PREFIX = FIXED_OBJECTIVE + " Next: "


@dataclass(frozen=True)
class RelayHandoff:
    node_id: str
    objective: str


@dataclass(frozen=True)
class TraceVerdict:
    retained: bool
    offending_node: str | None


def check_relay_trace(handoffs: Sequence[RelayHandoff]) -> TraceVerdict:
    """Return the first node whose objective loses the exact fixed prefix."""
    for handoff in handoffs:
        if not handoff.objective.startswith(_REQUIRED_PREFIX):
            return TraceVerdict(False, handoff.node_id)
    return TraceVerdict(True, None)
