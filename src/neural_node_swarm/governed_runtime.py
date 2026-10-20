"""Evidence-gated inheritance wired into the real Orchestrator.

The host owns the PromotionGate (and through it the InheritanceStore). Successor
nodes receive only a GatedReader, which can return the value of the latest
promoted lineage's active source for a region, or None to abstain. Accepted but
unpromoted records are never visible to successors.
"""
from __future__ import annotations

from typing import Any, Callable

from .inheritance_store import InheritanceStore
from .memory import MemoryStore
from .orchestrator import Orchestrator
from .poisoning_experiment import Candidate


class GatedReader:
    """Read-only view of promoted state. Its only public method is read()."""

    __slots__ = ("_read",)

    def __init__(self, read: Callable[[str], int | None]):
        object.__setattr__(self, "_read", read)

    def __setattr__(self, name, value):
        raise AttributeError("GatedReader is read-only")

    def __delattr__(self, name):
        raise AttributeError("GatedReader is read-only")

    def read(self, region: str) -> int | None:
        return self._read(region)

    def __repr__(self) -> str:
        return "GatedReader()"


class PromotionGate:
    """Host-side boundary: commits go through accept-then-promote."""

    def __init__(self, store: InheritanceStore, run_id: str):
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("run_id must be a non-empty string")
        self._store = store
        self._run_id = run_id

    def commit(self, region: str, value: int, evidence_ref: str, evidence: dict[str, int]) -> dict[str, Any]:
        active = self._store.accept(self._run_id, region, value, evidence_ref)
        audit, lineage = self._store.promote(self._run_id, region, active["id"], evidence)
        return {"active": active, "audit": audit, "lineage": lineage, "promoted": lineage is not None}

    def reader(self) -> GatedReader:
        store, run_id = self._store, self._run_id

        def read(region: str) -> int | None:
            lineage = store.read("lineage", run_id, region)
            if lineage is None:
                return None
            active = store.read("active", run_id, region, lineage["source_id"])
            return active["value"] if active is not None else None

        return GatedReader(read)


RUN_ID = "poisoning-experiment-v1"
REGIONS = ("hand", "leg")


def _successor_factory(reads: list[dict[str, Any]]):
    """Deterministic replacement worker: reads promoted state once, records it in valid outputs."""

    def factory(node_id: str, reader: GatedReader):
        generation = int(node_id.rsplit("-", 1)[1])
        observed: dict[str, int | None] = {}

        def fire(current: dict[str, Any], round_number: int) -> dict[str, Any]:
            if round_number == 1:
                for region in REGIONS:
                    observed[region] = reader.read(region)
                    reads.append({"generation": generation, "region": region, "value": observed[region]})
            constraints = [f"read:{region}={'abstain' if observed[region] is None else observed[region]}"
                           for region in REGIONS]
            return {"schema_version": "1.0", "step_id": f"{node_id}:{round_number}",
                    "objective": f"Report promoted regional state ({node_id}, round {round_number})",
                    "success_criteria": ["next objective is schema-valid"],
                    "required_memory_refs": [], "constraints": constraints}

        return fire

    return factory


def run_runtime_poisoning(db_path, memory: MemoryStore, *, poisoned: bool, evidence_fault: bool,
                          successors: int = 6) -> dict[str, Any]:
    """Run the regional poisoning scenario through Orchestrator.run_governed_chain."""
    if type(successors) is not int or successors < 1:
        raise ValueError("successors must be a positive integer")
    # Ground truth is evaluator-only; successors see only the gated reader.
    truth = {"hand": 10, "leg": 20}
    evidence = {"record:hand": 90 if evidence_fault else 10, "record:leg": 20}
    gate = PromotionGate(InheritanceStore(db_path), RUN_ID)
    commits = []
    for candidate in (Candidate("c-hand", "hand", 90 if poisoned else 10, "record:hand"),
                      Candidate("c-leg", "leg", 20, "record:leg")):
        result = gate.commit(candidate.region, candidate.value, candidate.evidence_ref, evidence)
        commits.append({"candidate_id": candidate.candidate_id, "region": candidate.region,
                        "promoted": result["promoted"]})
    reads: list[dict[str, Any]] = []
    Orchestrator(memory, lambda *_: {}).run_governed_chain(
        "Read inherited regional state through the promotion gate",
        node_factory=_successor_factory(reads), gate=gate, node_count=successors)
    wrong = sum(r["value"] is not None and r["value"] != truth[r["region"]] for r in reads)
    correct = sum(r["value"] == truth[r["region"]] for r in reads)
    return {"policy": "governed_runtime", "poisoned": poisoned, "evidence_fault": evidence_fault,
            "successors": successors, "commits": commits, "reads": reads,
            "metrics": {"wrong_successor_reads": wrong,
                        "correct_successor_reads": correct,
                        "abstentions": len(reads) - wrong - correct}}
