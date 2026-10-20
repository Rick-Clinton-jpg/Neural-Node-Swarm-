"""Deterministic paired cases for grounded and structural verdicts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from neural_node_swarm.lab.grounded_verdict import (
    CandidateAction,
    EvidenceRecord,
    Objective,
    Verdict,
    assess_progress,
)


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    objective: Objective
    candidate: CandidateAction
    evidence: Mapping[str, EvidenceRecord]


@dataclass(frozen=True)
class PairedVerdict:
    grounded: Verdict
    schema_only: Verdict


def assess_schema_only(
    objective: Objective,
    candidate: CandidateAction,
    evidence: Mapping[str, EvidenceRecord],
) -> Verdict:
    """Check field shape without comparing the claim with supplied evidence."""
    del evidence  # Accepted for the same policy interface; deliberately unexamined.
    fields = (
        objective.objective_id,
        objective.statement,
        candidate.action_id,
        candidate.objective_id,
        candidate.evidence_ref,
    )
    if not all(isinstance(value, str) and value for value in fields):
        return Verdict(False, "invalid field")
    if candidate.claimed_outcome not in {"progress", "complete"}:
        return Verdict(False, "invalid claimed outcome")
    return Verdict(True, "fields are valid")


def benchmark_cases() -> tuple[BenchmarkCase, ...]:
    """Build fresh, repeatable inputs, including a contradicted completion claim."""
    objective = Objective("relay", "Build an objective-retention benchmark")
    return tuple(
        BenchmarkCase(
            case_id,
            objective,
            CandidateAction("action-1", "relay", claim, "record-1"),
            {"record-1": EvidenceRecord("record-1", "action-1", "relay", "progress")},
        )
        for case_id, claim in (
            ("observed-progress", "progress"),
            ("unsupported-completion", "complete"),
        )
    )


def run_case(case: BenchmarkCase) -> PairedVerdict:
    """Give both policies the exact same candidate and evidence objects."""
    return PairedVerdict(
        grounded=assess_progress(case.objective, case.candidate, case.evidence),
        schema_only=assess_schema_only(case.objective, case.candidate, case.evidence),
    )
