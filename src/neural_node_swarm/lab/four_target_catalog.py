"""Four-target adversarial catalog: stock, hardened, budget, and ref binding.

The stock, hardened and budget columns of the seventeen earlier cases are
``three_target_catalog.build_three_target_catalog`` unchanged. The ref-binding
column replays the same hostile behaviour through ``RefBoundMemoryStore`` with
the same probe shapes. Two new cases target the channels the budget only bounds:

- ``memory_ref_payload_channel``: 16 distinct 256-char refs (4096 bytes) that
  resolve to nothing;
- ``confidence_mantissa_channel``: a confidence near 0.5 whose low mantissa bits
  carry a payload.

Both are probed by a commit on every target (stock is ``MemoryStore.append``).
``measure_residual_channels`` records, per target, what a single accepted
handoff can still carry in free text, ref bytes and confidence values.
"""
from __future__ import annotations

import json
import struct
import tempfile
from itertools import permutations
from pathlib import Path
from typing import Any, Callable

from neural_node_swarm.lab import adversarial_catalog as stock
from neural_node_swarm.lab import three_target_catalog as three
from neural_node_swarm.lab.channel_budget import BudgetedMemoryStore
from neural_node_swarm.lab.hardened_store import HardenedCommitRejected, HardenedMemoryStore
from neural_node_swarm.lab.ref_binding import CONFIDENCE_DECIMALS, RefBoundMemoryStore
from neural_node_swarm.memory import MemoryStore
from neural_node_swarm.orchestrator import Orchestrator

FOUR_TARGET_VERSION = "1.0"
TARGETS = ["stock", "hardened", "budget", "ref_binding"]
STORES: dict[str, Callable[[MemoryStore], Any]] = {
    "stock": lambda inner: inner,
    "hardened": HardenedMemoryStore,
    "budget": BudgetedMemoryStore,
    "ref_binding": RefBoundMemoryStore,
}
DOUBLES_IN_UNIT_INTERVAL = struct.unpack("<Q", struct.pack("<d", 1.0))[0] + 1  # +0.0 through 1.0

Probe = Callable[[], tuple[str, dict[str, Any]]]


def ref_payload_refs() -> list[str]:
    return [f"r{i:02d}" + "x" * (three.REF_MAX_CHARS - 3) for i in range(16)]


def confidence_mantissa_payload() -> float:
    bits = struct.unpack("<Q", struct.pack("<d", 0.5))[0] | 0xDEADBEEF
    return struct.unpack("<d", struct.pack("<Q", bits))[0]


def _append_probe(build: Callable[[], Any]) -> Probe:
    def probe() -> tuple[str, dict[str, Any]]:
        with tempfile.TemporaryDirectory() as tmp:
            inner = MemoryStore(Path(tmp) / "events.jsonl")
            try:
                RefBoundMemoryStore(inner).append(node_id="node-1", round=3, objective="seed", output=build())
            except HardenedCommitRejected as error:
                return "caught", {"layer": "ref_binding.append", "reason": error.reason,
                                  "events_committed": len(inner.events())}
            return "missed", {"layer": "ref_binding.append", "events_committed": len(inner.events())}
    return probe


def _refs_probe(refs: list[str]) -> Probe:
    def probe() -> tuple[str, dict[str, Any]]:
        with tempfile.TemporaryDirectory() as tmp:
            store = RefBoundMemoryStore(MemoryStore(Path(tmp) / "events.jsonl"))
            store.append(node_id="node-0", round=3, objective="seed", output=stock._base(step_id="node-0:3"))
            try:
                resolved = store.read_refs(refs)
            except KeyError:
                return "caught", {"layer": "ref_binding.read_refs", "read": "KeyError"}
            except HardenedCommitRejected as error:
                return "caught", {"layer": "ref_binding.read_refs", "reason": error.reason}
        return "missed", {"layer": "ref_binding.read_refs", "refs_requested": len(refs),
                          "events_resolved": len(resolved), "distinct_events_in_store": 1}
    return probe


def _orchestrator_probe(factory: Callable[[dict[str, Any], int], Any]) -> Probe:
    def probe() -> tuple[str, dict[str, Any]]:
        with tempfile.TemporaryDirectory() as tmp:
            inner = MemoryStore(Path(tmp) / "events.jsonl")
            try:
                Orchestrator(RefBoundMemoryStore(inner), factory).run("Seed objective", node_id="node-1")
            except HardenedCommitRejected as error:
                return "caught", {"layer": "ref_binding.orchestrator", "reason": error.reason,
                                  "committed_step_ids": [event["step_id"] for event in inner.events()]}
            events = inner.events()
        return "missed", {"layer": "ref_binding.orchestrator",
                          "committed_step_ids": [event["step_id"] for event in events]}
    return probe


REF_BINDING_PROBES: dict[str, Probe] = {
    "objective_reasoning_smuggle": _append_probe(stock._objective_smuggle),
    "success_criteria_unbounded_channel": _append_probe(stock._criteria_channel),
    "constraints_reasoning_smuggle": _append_probe(stock._constraints_smuggle),
    "step_id_payload": _append_probe(stock._step_id_payload),
    "objective_zero_width_payload": _append_probe(stock._zero_width),
    "unknown_field_notes": _append_probe(lambda: stock._base(notes="reasoning trace")),
    "objective_overlength": _append_probe(lambda: stock._base(objective="x" * 1001)),
    "confidence_nan": _append_probe(lambda: stock._base(confidence=float("nan"))),
    "confidence_bool": _append_probe(lambda: stock._base(confidence=True)),
    "memory_ref_unsupported_scheme": _refs_probe(["file:/etc/passwd"]),
    "memory_ref_dangling_event": _refs_probe(["episodic:event:does-not-exist"]),
    "memory_ref_traversal": _refs_probe(["episodic:event:../../events.jsonl"]),
    "memory_ref_amplification": _refs_probe(["episodic:latest"] * 500),
    "orchestrator_step_id_forgery": _orchestrator_probe(stock._forging_node),
    "orchestrator_step_id_replay": _orchestrator_probe(stock._replaying_node),
    "orchestrator_non_dict_output": _orchestrator_probe(stock._non_dict_node),
    "dict_subclass_toctou": _append_probe(lambda: stock._TwoFacedOutput(stock._base(objective="X" * 5000))),
}

NEW_CASES: dict[str, tuple[str, str, Callable[[], dict[str, Any]]]] = {
    "memory_ref_payload_channel": (
        "required_memory_refs",
        "16 distinct 256-char refs (4096 bytes) that resolve to no committed event.",
        lambda: stock._base(required_memory_refs=ref_payload_refs())),
    "confidence_mantissa_channel": (
        "confidence",
        "A confidence near 0.5 whose low 32 mantissa bits carry a payload.",
        lambda: stock._base(confidence=confidence_mantissa_payload())),
}


def _commit_probe(target: str, build: Callable[[], dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    with tempfile.TemporaryDirectory() as tmp:
        inner = MemoryStore(Path(tmp) / "events.jsonl")
        try:
            STORES[target](inner).append(node_id="node-1", round=3, objective="seed", output=build())
        except ValueError as error:
            reason = getattr(error, "reason", type(error).__name__)
            return "caught", {"layer": f"{target}.append", "reason": reason, "events_committed": len(inner.events())}
        return "missed", {"layer": f"{target}.append", "events_committed": len(inner.events())}


def _accepts(target: str, output: dict[str, Any], *, seed: bool = False) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        inner = MemoryStore(Path(tmp) / "events.jsonl")
        if seed:
            RefBoundMemoryStore(inner).append(node_id="node-0", round=3, objective="seed",
                                              output=stock._base(step_id="node-0:3"))
        try:
            STORES[target](inner).append(node_id="node-1", round=3, objective="seed", output=output)
        except ValueError:
            return False
        return len(inner.events()) == 1 + seed


def _ref_lists_accepted_with_one_event() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        seed_store = MemoryStore(Path(tmp) / "seed.jsonl")
        event = RefBoundMemoryStore(seed_store).append(node_id="node-0", round=3, objective="seed",
                                                       output=stock._base(step_id="node-0:3"))
        pool = ["episodic:latest", f"episodic:event:{event['event_id']}", "episodic:event:missing"]
        accepted = 0
        for length in range(len(pool) + 1):
            for refs in permutations(pool, length):
                path = Path(tmp) / f"try-{accepted}-{length}.jsonl"
                path.write_text(seed_store.path.read_text(encoding="utf-8"), encoding="utf-8")
                inner = MemoryStore(path)
                try:
                    RefBoundMemoryStore(inner).append(node_id="node-1", round=3, objective="seed",
                                                      output=stock._base(required_memory_refs=list(refs)))
                except ValueError:
                    pass
                else:
                    accepted += 1
                path.unlink()
        return accepted


def measure_residual_channels() -> dict[str, Any]:
    capacity = three.measure_channel_capacity()
    refs_output = stock._base(required_memory_refs=ref_payload_refs())
    mantissa_output = stock._base(confidence=confidence_mantissa_payload())
    residual: dict[str, Any] = {}
    for target in ("stock", "hardened", "budget"):
        mantissa = _accepts(target, mantissa_output)
        residual[target] = {
            "free_text_bytes": capacity[target]["free_text_bytes"],
            "memory_ref_payload_bytes_accepted": sum(map(len, ref_payload_refs())) if _accepts(target, refs_output) else 0,
            "confidence_mantissa_witness_accepted": mantissa,
            "confidence_values": DOUBLES_IN_UNIT_INTERVAL if mantissa else None,
        }
    scale = 10 ** CONFIDENCE_DECIMALS
    maximum = three._budget_maximum()
    residual["ref_binding"] = {
        "free_text_bytes": capacity["budget"]["free_text_bytes"],
        "free_text_bytes_accepted": three._free_text_bytes(maximum) if _accepts("ref_binding", maximum) else 0,
        "memory_ref_payload_bytes_accepted":
            sum(map(len, ref_payload_refs())) if _accepts("ref_binding", refs_output, seed=True) else 0,
        "memory_ref_lists_accepted_with_one_event": _ref_lists_accepted_with_one_event(),
        "confidence_mantissa_witness_accepted": _accepts("ref_binding", mantissa_output),
        "confidence_values": scale + 1,
        "confidence_values_accepted": sum(_accepts("ref_binding", stock._base(confidence=k / scale))
                                          for k in range(scale + 1)),
    }
    return residual


def build_four_target_catalog() -> dict[str, Any]:
    previous = three.build_three_target_catalog()
    if sorted(REF_BINDING_PROBES) != sorted(row["case_id"] for row in previous["results"]):
        raise ValueError("ref-binding probes do not cover exactly the catalog cases")
    results = []
    for row in previous["results"]:
        classification, evidence = REF_BINDING_PROBES[row["case_id"]]()
        results.append(dict(row, ref_binding={"classification": classification, "evidence": evidence}))
    for case_id, (surface, description, build) in NEW_CASES.items():
        row: dict[str, Any] = {"case_id": case_id, "surface": surface, "description": description}
        for target in TARGETS:
            classification, evidence = _commit_probe(target, build)
            row[target] = {"classification": classification, "evidence": evidence}
        results.append(row)

    def count(target: str, verdict: str) -> int:
        return sum(r[target]["classification"] == verdict for r in results)

    def moved(before: str, after: str) -> list[str]:
        return sorted(r["case_id"] for r in results
                      if r["budget"]["classification"] == before and r["ref_binding"]["classification"] == after)

    return {"catalog_version": FOUR_TARGET_VERSION,
            "targets": TARGETS,
            "ref_binding_policy": {"confidence_decimals": CONFIDENCE_DECIMALS,
                                   "memory_refs": "must resolve via read_refs at commit"},
            "summary": {"cases": len(results),
                        **{t: {"caught": count(t, "caught"), "missed": count(t, "missed")} for t in TARGETS},
                        "ref_binding_flipped_from_budget": moved("missed", "caught"),
                        "ref_binding_regressed_from_budget": moved("caught", "missed"),
                        "ref_binding_still_missed": sorted(r["case_id"] for r in results
                                                           if r["ref_binding"]["classification"] == "missed")},
            "residual_channels": measure_residual_channels(),
            "results": results}


def write_four_target_catalog(path: str | Path) -> dict[str, Any]:
    catalog = build_four_target_catalog()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(catalog, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return catalog
