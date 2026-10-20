"""Multi-hop contamination with a matched-cost policy comparison.

Deterministic mechanism experiment, not a benchmark of model intelligence.

The host commits hand=10 and leg=20, then runs successors through
Orchestrator.run_governed_chain. Each successor reads both regions through a
read-only GatedReader and records the reads in three valid node outputs. Only
generation ``inject_at`` proposes hand=90 (evidence_ref record:hand), in its
round-3 output. The host factory, called once per generation, reads the previous
successor's committed output through MemoryStore and commits any proposal before
building the next successor, so contamination enters ``inject_at`` hops deep.

Scenarios: ``clean`` (no proposal), ``poisoned`` (evidence says hand=10),
``corrupted`` (the evidence record for hand is corrupted to 90 when the proposal
arrives). Policies: ``promotion`` (PromotionGate over InheritanceStore),
``gated_shared`` (a shared dict whose writes are applied only if the same
evidence-equality check passes) and ``shared`` (ungated baseline). Promotion and
gated_shared perform one evidence check per write, so their check cost matches.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .governed_runtime import GatedReader, PromotionGate
from .inheritance_store import InheritanceStore
from .memory import MemoryStore
from .orchestrator import Orchestrator

POLICIES = ("promotion", "gated_shared", "shared")
SCENARIOS = ("clean", "poisoned", "corrupted")
REGIONS = ("hand", "leg")
TRUTH = {"hand": 10, "leg": 20}
POISON = {"region": "hand", "value": 90, "evidence_ref": "record:hand"}
RUN_ID = "multi-hop-matched-cost-v1"

DOES_NOT_SHOW = [
    "No model workers: successors are deterministic readers, so this says nothing about model behaviour.",
    "Only one region (hand) is injected, once, by one generation; repeated or multi-region attacks are untested.",
    "Equal outcomes for promotion and gated_shared at matched cost are not a general equivalence claim.",
    "The evidence check only compares integers against a trusted host registry; it is not semantic verification.",
    "Corrupted evidence defeats both gated policies; neither detects a compromised evidence source.",
]


def _evidence_passes(evidence: dict[str, int], evidence_ref: str, value: int) -> bool:
    # Same rule as InheritanceStore.promote (evidence_equal_v1).
    expected = evidence.get(evidence_ref)
    return type(expected) is int and expected == value


class _PromotionPolicy:
    def __init__(self, db_path, run_id: str):
        self._gate = PromotionGate(InheritanceStore(db_path), run_id)
        self.evidence_checks = 0

    def commit(self, region, value, evidence_ref, evidence) -> bool:
        self.evidence_checks += 1  # PromotionGate.commit runs exactly one promote() check
        return self._gate.commit(region, value, evidence_ref, evidence)["promoted"]

    def reader(self) -> GatedReader:
        return self._gate.reader()


class _SharedPolicy:
    def __init__(self, gated: bool):
        self._gated = gated
        self._state: dict[str, int] = {}
        self.evidence_checks = 0

    def commit(self, region, value, evidence_ref, evidence) -> bool:
        if self._gated:
            self.evidence_checks += 1
            if not _evidence_passes(evidence, evidence_ref, value):
                return False
        self._state[region] = value
        return True

    def reader(self) -> GatedReader:
        state = self._state
        return GatedReader(lambda region: state.get(region))


def _parse_proposal(output: dict[str, Any]) -> dict[str, Any] | None:
    for constraint in output.get("constraints", []):
        if constraint.startswith("propose:"):
            assignment, _, evidence_ref = constraint.removeprefix("propose:").partition("@")
            region, _, value = assignment.partition("=")
            return {"region": region, "value": int(value), "evidence_ref": evidence_ref}
    return None


def _successor(node_id: str, generation: int, reader: GatedReader, propose: bool,
               reads: list[dict[str, Any]]):
    observed: dict[str, int | None] = {}

    def fire(current: dict[str, Any], round_number: int) -> dict[str, Any]:
        if round_number == 1:
            for region in REGIONS:
                observed[region] = reader.read(region)
                reads.append({"generation": generation, "region": region, "value": observed[region]})
        constraints = [f"read:{region}={'abstain' if observed[region] is None else observed[region]}"
                       for region in REGIONS]
        if propose and round_number == 3:
            constraints.append(f"propose:{POISON['region']}={POISON['value']}@{POISON['evidence_ref']}")
        return {"schema_version": "1.0", "step_id": f"{node_id}:{round_number}",
                "objective": f"Report inherited regional state ({node_id}, round {round_number})",
                "success_criteria": ["next objective is schema-valid"],
                "required_memory_refs": [], "constraints": constraints}

    return fire


def run_multi_hop(db_path, memory: MemoryStore, *, policy: str, scenario: str,
                  generations: int = 6, inject_at: int = 3) -> dict[str, Any]:
    if policy not in POLICIES:
        raise ValueError("unknown policy")
    if scenario not in SCENARIOS:
        raise ValueError("unknown scenario")
    if type(generations) is not int or generations < 2:
        raise ValueError("generations must be an integer of at least 2")
    if type(inject_at) is not int or not 1 <= inject_at < generations:
        raise ValueError("inject_at must satisfy 1 <= inject_at < generations")

    # Trusted host evidence registry; ground truth (TRUTH) is evaluator-only.
    evidence = {"record:hand": 10, "record:leg": 20}
    if policy == "promotion":
        store: Any = _PromotionPolicy(db_path, RUN_ID)
    else:
        store = _SharedPolicy(gated=policy == "gated_shared")
    for region in REGIONS:
        store.commit(region, TRUTH[region], f"record:{region}", evidence)

    reads: list[dict[str, Any]] = []
    proposals: list[dict[str, Any]] = []

    def host_factory(node_id: str, reader: GatedReader):
        generation = int(node_id.rsplit("-", 1)[1])
        if generation > 1:
            latest = memory.read_refs(["episodic:latest"])
            proposal = _parse_proposal(latest[0]["output"]) if latest else None
            if proposal is not None:
                if scenario == "corrupted":
                    evidence[proposal["evidence_ref"]] = proposal["value"]
                applied = store.commit(proposal["region"], proposal["value"],
                                       proposal["evidence_ref"], evidence)
                proposals.append({"generation": generation - 1, **proposal, "applied": applied})
        propose = scenario != "clean" and generation == inject_at
        return _successor(node_id, generation, reader, propose, reads)

    Orchestrator(memory, lambda *_: {}).run_governed_chain(
        "Read inherited regional state across successor generations",
        node_factory=host_factory, gate=store, node_count=generations)

    wrong_generations = sorted({r["generation"] for r in reads
                                if r["value"] is not None and r["value"] != TRUTH[r["region"]]})
    wrong = sum(r["value"] is not None and r["value"] != TRUTH[r["region"]] for r in reads)
    correct = sum(r["value"] == TRUTH[r["region"]] for r in reads)
    return {"policy": policy, "scenario": scenario, "generations": generations,
            "inject_at": inject_at, "reads": reads, "proposals": proposals,
            "metrics": {"wrong": wrong, "correct": correct, "abstentions": len(reads) - wrong - correct},
            "first_contaminated_generation": wrong_generations[0] if wrong_generations else None,
            "evidence_checks": store.evidence_checks,
            "does_not_show": list(DOES_NOT_SHOW)}


def compare(directory, *, generations: int = 6, inject_at: int = 3) -> dict[str, Any]:
    """Run every policy x scenario in fresh stores and report matched check cost."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    cases = []
    for scenario in SCENARIOS:
        for policy in POLICIES:
            memory = MemoryStore(directory / f"compare-{policy}-{scenario}.jsonl")
            cases.append(run_multi_hop(directory / f"compare-{policy}-{scenario}.db", memory,
                                       policy=policy, scenario=scenario,
                                       generations=generations, inject_at=inject_at))
    matched = {}
    for scenario in SCENARIOS:
        cost = {c["policy"]: c["evidence_checks"] for c in cases if c["scenario"] == scenario}
        matched[scenario] = {"promotion": cost["promotion"], "gated_shared": cost["gated_shared"],
                             "equal": cost["promotion"] == cost["gated_shared"]}
    if not all(entry["equal"] for entry in matched.values()):
        raise AssertionError(f"evidence check cost is not matched: {matched}")
    return {"experiment_version": "1.0", "generations": generations, "inject_at": inject_at,
            "cases": cases, "matched_cost": matched, "does_not_show": list(DOES_NOT_SHOW)}
