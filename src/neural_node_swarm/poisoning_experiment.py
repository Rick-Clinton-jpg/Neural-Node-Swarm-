"""Deterministic mechanism experiment, not a benchmark of model intelligence.

Run: python -m neural_node_swarm.poisoning_experiment
Both policies receive identical observations, evidence, and checks. Only the
decision to require evidence for durable inheritance differs.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass

from pathlib import Path

from .inheritance_store import InheritanceStore
from .memory import MemoryStore
from .orchestrator import Orchestrator


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    region: str
    value: int
    evidence_ref: str


def checks(candidate: Candidate, evidence: dict[str, int]) -> dict[str, bool]:
    return {
        "immediate": type(candidate.value) is int and 0 <= candidate.value <= 100,
        "evidence": evidence.get(candidate.evidence_ref) == candidate.value,
    }


def run_case(policy: str, *, poisoned: bool = True, evidence_fault: bool = False,
             successors: int = 6) -> dict:
    if policy not in {"shared", "promotion"}:
        raise ValueError("unknown policy")
    if type(successors) is not int or successors < 1:
        raise ValueError("successors must be a positive integer")
    # Ground truth is evaluator-only; policy code sees the evidence registry.
    truth = {"hand": 10, "leg": 20}
    evidence = {"record:hand": 90 if evidence_fault else 10, "record:leg": 20}
    candidates = [Candidate("c-hand", "hand", 90 if poisoned else 10, "record:hand"),
                  Candidate("c-leg", "leg", 20, "record:leg")]
    durable, active, audit = {}, {}, []
    for candidate in candidates:
        result = checks(candidate, evidence)
        accepted = result["immediate"]
        if accepted:
            active[candidate.region] = candidate.value
        inherited = accepted and (policy == "shared" or result["evidence"])
        event_id = f"decision:{candidate.candidate_id}"
        audit.append({"event_id": event_id, "candidate": asdict(candidate),
                      "checks": result, "inherited": inherited,
                      "kind": "promotion" if policy == "promotion" else "shared_write"})
        if inherited:
            durable[candidate.region] = {"value": candidate.value,
                                          "decision_ref": event_id}
    # Identical replacement-worker rule: read durable state only, abstain if absent.
    reads = []
    for generation in range(1, successors + 1):
        for region in truth:
            state = durable.get(region)
            reads.append({"generation": generation, "region": region,
                          "value": state["value"] if state else None,
                          "decision_ref": state["decision_ref"] if state else None})
    wrong = sum(r["value"] is not None and r["value"] != truth[r["region"]] for r in reads)
    correct = sum(r["value"] == truth[r["region"]] for r in reads)
    return {"policy": policy, "poisoned": poisoned, "evidence_fault": evidence_fault,
            "successors": successors, "active": active, "durable": durable,
            "audit": audit, "reads": reads,
            "metrics": {"wrong_successor_reads": wrong,
                        "correct_successor_reads": correct,
                        "abstentions": len(reads) - wrong - correct,
                        "contaminated_durable_regions": sum(
                            v["value"] != truth[k] for k, v in durable.items())}}


def experiment(successors: int = 6) -> dict:
    scenarios = [("clean", False, False), ("poison", True, False),
                 ("poison_and_corrupt_evidence", True, True)]
    return {"experiment_version": "1.0", "worker": "deterministic durable-memory reader",
            "cases": [{"scenario": name, **run_case(policy, poisoned=poison,
                       evidence_fault=fault, successors=successors)}
                      for name, poison, fault in scenarios
                      for policy in ("shared", "promotion")]}


def run_persistent_promotion(path, *, poisoned=True, evidence_fault=False, successors=6):
    """Exercise the governed policy through persisted active/audit/lineage records."""
    if type(successors) is not int or successors < 1:
        raise ValueError("successors must be a positive integer")
    truth = {"hand": 10, "leg": 20}
    evidence = {"record:hand": 90 if evidence_fault else 10, "record:leg": 20}
    store = InheritanceStore(path)
    run_id = "poisoning-experiment-v1"
    for candidate in (Candidate("c-hand", "hand", 90 if poisoned else 10, "record:hand"),
                      Candidate("c-leg", "leg", 20, "record:leg")):
        active = store.accept(run_id, candidate.region, candidate.value, candidate.evidence_ref)
        store.promote(run_id, candidate.region, active["id"], evidence)
    reopened = InheritanceStore(path)
    reads = []
    for generation in range(1, successors + 1):
        for region in truth:
            lineage = reopened.read("lineage", run_id, region)
            active = reopened.read("active", run_id, region, lineage["source_id"]) if lineage else None
            reads.append({"generation": generation, "region": region,
                          "value": active["value"] if active else None,
                          "lineage_id": lineage["id"] if lineage else None})
    wrong = sum(item["value"] is not None and item["value"] != truth[item["region"]] for item in reads)
    correct = sum(item["value"] == truth[item["region"]] for item in reads)
    return {"policy": "persistent_promotion", "reads": reads,
            "metrics": {"wrong_successor_reads": wrong,
                        "correct_successor_reads": correct,
                        "abstentions": len(reads) - wrong - correct}}


def run_governed_runtime(directory, *, poisoned=True, evidence_fault=False, successors=6):
    """Run the regional scenario through Orchestrator.run_chain.

    Successor nodes are built only from InheritanceStore.inherited, the
    evidence-gated promotion boundary; None means the node abstains.
    """
    if type(successors) is not int or successors < 1:
        raise ValueError("successors must be a positive integer")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    truth = {"hand": 10, "leg": 20}
    evidence = {"record:hand": 90 if evidence_fault else 10, "record:leg": 20}
    store = InheritanceStore(directory / "inheritance.db")
    run_id = "poisoning-experiment-runtime-v1"
    for candidate in (Candidate("c-hand", "hand", 90 if poisoned else 10, "record:hand"),
                      Candidate("c-leg", "leg", 20, "record:leg")):
        active = store.accept(run_id, candidate.region, candidate.value, candidate.evidence_ref)
        store.promote(run_id, candidate.region, active["id"], evidence)
    orchestrator = Orchestrator(MemoryStore(directory / "events.jsonl"), lambda *_: {})
    reads, final_step_ids = [], {}
    for region in truth:
        def factory(node_id, inherited, region=region):
            reads.append({"generation": len([r for r in reads if r["region"] == region]) + 1,
                          "region": region, "node_id": node_id, "value": inherited})

            def fire(current, round_number):
                return {"schema_version": "1.0", "step_id": f"{node_id}:{round_number}",
                        "objective": f"{region} successor {node_id} round {round_number}",
                        "success_criteria": ["next objective is schema-valid"],
                        "required_memory_refs": ["episodic:latest"]}
            return fire
        final = orchestrator.run_chain(f"regional state for {region}", node_factory=factory,
                                       node_count=successors, inheritance=store,
                                       scope=(run_id, region))
        final_step_ids[region] = final["step_id"]
    wrong = sum(r["value"] is not None and r["value"] != truth[r["region"]] for r in reads)
    correct = sum(r["value"] == truth[r["region"]] for r in reads)
    contaminated = sum(any(r["region"] == region and r["value"] is not None and r["value"] != value
                           for r in reads) for region, value in truth.items())
    return {"policy": "governed_runtime", "poisoned": poisoned, "evidence_fault": evidence_fault,
            "successors": successors, "reads": reads, "final_step_ids": final_step_ids,
            "metrics": {"wrong_successor_reads": wrong,
                        "correct_successor_reads": correct,
                        "abstentions": len(reads) - wrong - correct,
                        "contaminated_regions": contaminated}}


def governed_runtime_experiment(directory, successors: int = 6) -> dict:
    """Clean, poisoned and corrupted-evidence controls through the real runtime path."""
    scenarios = [("clean", False, False), ("poison", True, False),
                 ("poison_and_corrupt_evidence", True, True)]
    return {"experiment_version": "1.0", "worker": "Orchestrator.run_chain successor",
            "cases": [{"scenario": name, **run_governed_runtime(Path(directory) / name, poisoned=poison,
                                                                evidence_fault=fault,
                                                                successors=successors)}
                      for name, poison, fault in scenarios]}


class SharedGatedMemory:
    """Shared memory whose writes are unchecked; each read checks evidence once.

    The latest write is what a successor sees. If it disagrees with the evidence
    the successor abstains (None). Used through Orchestrator.run_chain's
    ``inheritance`` hook, so it exposes ``inherited(run_id, region)``.
    """

    def __init__(self, evidence_at):
        self.writes, self.checks, self.evidence_at, self.generation = [], 0, evidence_at, 0

    def write(self, value):
        self.writes.append(value)

    def inherited(self, run_id, region):
        self.generation += 1
        if not self.writes:
            return None
        self.checks += 1
        value = self.writes[-1]
        return value if self.evidence_at(self.generation) == value else None


MULTI_HOP_DOES_NOT_SHOW = [
    "Only one region, one poisoned generation and one evidence record are modeled.",
    "Matched cost counts evidence checks made by successors; the trusted seed "
    "promotion before the chain is not counted.",
    "When the evidence itself is corrupted both policies are contaminated; "
    "neither check detects a consistent lie.",
    "Workers are deterministic readers, not models; this says nothing about model behavior.",
    "Abstention is not recovery: a successor that abstains does not restore the true value.",
]


def run_multi_hop(directory, *, policy, inject_at, successors=6, evidence_fault=False):
    """Inject a poisoned write at successor generation ``inject_at`` (>1).

    Each generation runs as an Orchestrator.run_chain node, reads only through
    the policy's inheritance boundary, and writes back what it read (the
    poisoned generation writes the poison). Both policies spend exactly one
    evidence check per successor generation: ``promotion`` checks on write,
    ``shared_gated`` checks on read. With ``evidence_fault`` the evidence record
    is corrupted to the poison from generation ``inject_at`` on.
    """
    if policy not in {"promotion", "shared_gated"}:
        raise ValueError("unknown policy")
    if type(successors) is not int or successors < 1:
        raise ValueError("successors must be a positive integer")
    if type(inject_at) is not int or not 2 <= inject_at <= successors:
        raise ValueError("inject_at must be an integer between 2 and successors")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    truth, poison, run_id, region = 10, 90, "multi-hop-v1", "hand"

    def evidence_at(generation):
        return poison if evidence_fault and generation >= inject_at else truth

    checks_made = [0]
    if policy == "promotion":
        boundary = InheritanceStore(directory / "inheritance.db")
        seed = boundary.accept(run_id, region, truth, "record:hand")
        boundary.promote(run_id, region, seed["id"], {"record:hand": truth})

        def write(generation, value):
            active = boundary.accept(run_id, region, value, "record:hand")
            boundary.promote(run_id, region, active["id"], {"record:hand": evidence_at(generation)})
            checks_made[0] += 1
    else:
        boundary = SharedGatedMemory(evidence_at)
        boundary.write(truth)

        def write(generation, value):
            boundary.write(value)

    per_generation = []

    def factory(node_id, inherited):
        generation = len(per_generation) + 1
        outcome = "abstain" if inherited is None else ("correct" if inherited == truth else "wrong")
        per_generation.append({"generation": generation, "node_id": node_id,
                               "value": inherited, "outcome": outcome})
        written = poison if generation == inject_at else inherited
        if written is not None:
            write(generation, written)

        def fire(current, round_number):
            return {"schema_version": "1.0", "step_id": f"{node_id}:{round_number}",
                    "objective": f"{region} successor {node_id} round {round_number}",
                    "success_criteria": ["next objective is schema-valid"],
                    "required_memory_refs": ["episodic:latest"]}
        return fire

    Orchestrator(MemoryStore(directory / "events.jsonl"), lambda *_: {}).run_chain(
        "multi-hop regional state", node_factory=factory, node_count=successors,
        inheritance=boundary, scope=(run_id, region))
    checks = checks_made[0] if policy == "promotion" else boundary.checks
    outcomes = [g["outcome"] for g in per_generation]
    first_wrong = next((g["generation"] for g in per_generation if g["outcome"] == "wrong"), None)
    return {"policy": policy, "inject_at": inject_at, "successors": successors,
            "evidence_fault": evidence_fault, "per_generation": per_generation,
            "evidence_checks": checks,
            "metrics": {"wrong_successor_reads": outcomes.count("wrong"),
                        "correct_successor_reads": outcomes.count("correct"),
                        "abstentions": outcomes.count("abstain"),
                        "first_wrong_generation": first_wrong}}


def multi_hop_experiment(directory, inject_at: int = 3, successors: int = 6) -> dict:
    """Promotion vs evidence-gated shared memory at matched check cost."""
    cases = [{"scenario": name, **run_multi_hop(Path(directory) / name / policy, policy=policy,
                                                inject_at=inject_at, successors=successors,
                                                evidence_fault=fault)}
             for name, fault in (("poison", False), ("poison_and_corrupt_evidence", True))
             for policy in ("promotion", "shared_gated")]
    matched = all(len({c["evidence_checks"] for c in cases if c["scenario"] == name}) == 1
                  for name in ("poison", "poison_and_corrupt_evidence"))
    return {"experiment_version": "1.0", "worker": "Orchestrator.run_chain successor",
            "matched_check_cost": matched, "cases": cases,
            "does_not_show": list(MULTI_HOP_DOES_NOT_SHOW)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--successors", type=int, default=6)
    args = parser.parse_args()
    print(json.dumps(experiment(args.successors), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
