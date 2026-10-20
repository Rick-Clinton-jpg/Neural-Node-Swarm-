"""Compare a candidate's claim with an independently supplied observation.

The caller owns the evidence registry. This check establishes consistency with
that registry, not whether the observation itself is true.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class Objective:
    objective_id: str
    statement: str


@dataclass(frozen=True)
class CandidateAction:
    action_id: str
    objective_id: str
    claimed_outcome: str
    evidence_ref: str


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    action_id: str
    objective_id: str
    observed_outcome: str


@dataclass(frozen=True)
class Verdict:
    supported: bool
    reason: str


def assess_progress(
    objective: Objective,
    candidate: CandidateAction,
    evidence: Mapping[str, EvidenceRecord],
) -> Verdict:
    """Accept only a progress or completion claim backed by matching evidence."""
    if candidate.objective_id != objective.objective_id:
        return Verdict(False, "candidate targets another objective")
    if candidate.claimed_outcome not in {"progress", "complete"}:
        return Verdict(False, "unsupported claim")
    record = evidence.get(candidate.evidence_ref)
    if record is None:
        return Verdict(False, "missing evidence")
    if (
        record.evidence_id != candidate.evidence_ref
        or record.action_id != candidate.action_id
        or record.objective_id != objective.objective_id
    ):
        return Verdict(False, "evidence does not match candidate")
    if record.observed_outcome != candidate.claimed_outcome:
        return Verdict(False, "observed outcome does not support claim")
    return Verdict(True, "claim matches observation")
