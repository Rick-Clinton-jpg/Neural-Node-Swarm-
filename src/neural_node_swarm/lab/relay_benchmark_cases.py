"""Paired five-node traces for objective-prefix and structural checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from neural_node_swarm.lab.relay_trace import (
    FIXED_OBJECTIVE,
    RelayHandoff,
    TraceVerdict,
    check_relay_trace,
)


@dataclass(frozen=True)
class RelayCase:
    case_id: str
    handoffs: tuple[RelayHandoff, ...]


@dataclass(frozen=True)
class PairedTraceVerdict:
    prefix: TraceVerdict
    structural: TraceVerdict


def assess_trace_structure(handoffs: Sequence[RelayHandoff]) -> TraceVerdict:
    """Check five ordered, populated handoffs without reading objective meaning."""
    if len(handoffs) != 5:
        return TraceVerdict(False, None)
    for index, handoff in enumerate(handoffs, start=1):
        if handoff.node_id != f"node-{index}" or not isinstance(handoff.objective, str) or not handoff.objective:
            return TraceVerdict(False, handoff.node_id)
    return TraceVerdict(True, None)


def relay_cases() -> tuple[RelayCase, ...]:
    """Return fresh retained and prefix-drifted traces with identical structure."""
    retained = tuple(
        RelayHandoff(f"node-{index}", FIXED_OBJECTIVE + f" Next: complete step {index}.")
        for index in range(1, 6)
    )
    drifted = retained[:2] + (
        RelayHandoff(
            "node-3",
            "Build a reproducible objective-retention benchmark for a five-node relay, "
            "with distracting inputs and grounded checks. Next: complete step 3.",
        ),
    ) + retained[3:]
    return (
        RelayCase("retained-prefix", retained),
        RelayCase("drifted-prefix", drifted),
    )


def run_relay_case(case: RelayCase) -> PairedTraceVerdict:
    """Apply both checks to the same immutable handoff tuple."""
    return PairedTraceVerdict(
        prefix=check_relay_trace(case.handoffs),
        structural=assess_trace_structure(case.handoffs),
    )
