"""Deterministic counts of outcomes from the two paired-case fixtures."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from neural_node_swarm.lab.benchmark_cases import benchmark_cases, run_case
from neural_node_swarm.lab.relay_benchmark_cases import relay_cases, run_relay_case


@dataclass(frozen=True)
class PairCounts:
    total: int
    both_pass: int
    primary_only_pass: int
    control_only_pass: int
    both_fail: int


@dataclass(frozen=True)
class BenchmarkSummary:
    grounded: PairCounts
    relay: PairCounts


def _count_pairs(outcomes: Iterable[tuple[bool, bool]]) -> PairCounts:
    both = primary_only = control_only = neither = total = 0
    for primary, control in outcomes:
        total += 1
        if primary and control:
            both += 1
        elif primary:
            primary_only += 1
        elif control:
            control_only += 1
        else:
            neither += 1
    return PairCounts(total, both, primary_only, control_only, neither)


def summarize_paired_cases() -> BenchmarkSummary:
    """Count verdict combinations over fresh grounded and relay fixtures."""
    grounded = _count_pairs(
        (verdict.grounded.supported, verdict.schema_only.supported)
        for verdict in (run_case(case) for case in benchmark_cases())
    )
    relay = _count_pairs(
        (verdict.prefix.retained, verdict.structural.retained)
        for verdict in (run_relay_case(case) for case in relay_cases())
    )
    return BenchmarkSummary(grounded, relay)
