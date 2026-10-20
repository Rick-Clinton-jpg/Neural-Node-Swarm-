"""Two-target adversarial catalog: every case in the stock catalog, classified
against both the unmodified runtime and the hardened commit path.

The stock column is ``adversarial_catalog.run_case`` unchanged. The hardened
column replays the same hostile behaviour through ``HardenedMemoryStore``:

- schema side-channel cases are committed as ``node-1`` round 3 (their step_id
  claims ``node-1:3``); ``caught`` means the hardened append refused them.
- memory-reference cases read through the hardened store's ``read_refs``.
- orchestrator cases run the same hostile node factories with the hardened
  store in place of the stock one.
- the TOCTOU case appends the same two-faced dict subclass.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Callable

from neural_node_swarm.lab import adversarial_catalog as stock
from neural_node_swarm.lab.hardened_store import HardenedCommitRejected, HardenedMemoryStore
from neural_node_swarm.memory import MemoryStore
from neural_node_swarm.orchestrator import Orchestrator

TWO_TARGET_VERSION = "1.0"

Probe = Callable[[], tuple[str, dict[str, Any]]]


def _append_probe(build: Callable[[], Any]) -> Probe:
    def probe() -> tuple[str, dict[str, Any]]:
        with tempfile.TemporaryDirectory() as tmp:
            inner = MemoryStore(Path(tmp) / "events.jsonl")
            try:
                HardenedMemoryStore(inner).append(node_id="node-1", round=3, objective="seed", output=build())
            except HardenedCommitRejected as error:
                return "caught", {"layer": "hardened.append", "reason": error.reason, "events_committed": len(inner.events())}
            return "missed", {"layer": "hardened.append", "events_committed": len(inner.events())}
    return probe


def _refs_probe(refs: list[str]) -> Probe:
    def probe() -> tuple[str, dict[str, Any]]:
        with tempfile.TemporaryDirectory() as tmp:
            store = HardenedMemoryStore(MemoryStore(Path(tmp) / "events.jsonl"))
            store.append(node_id="node-0", round=3, objective="seed", output=stock._base(step_id="node-0:3"))
            try:
                resolved = store.read_refs(refs)
            except KeyError:
                return "caught", {"layer": "hardened.read_refs", "read": "KeyError"}
        return "missed", {"layer": "hardened.read_refs", "refs_requested": len(refs), "events_resolved": len(resolved),
                          "distinct_events_in_store": 1}
    return probe


def _orchestrator_probe(factory: Callable[[dict[str, Any], int], Any]) -> Probe:
    def probe() -> tuple[str, dict[str, Any]]:
        with tempfile.TemporaryDirectory() as tmp:
            inner = MemoryStore(Path(tmp) / "events.jsonl")
            try:
                Orchestrator(HardenedMemoryStore(inner), factory).run("Seed objective", node_id="node-1")
            except HardenedCommitRejected as error:
                return "caught", {"layer": "hardened.orchestrator", "reason": error.reason,
                                  "committed_step_ids": [event["step_id"] for event in inner.events()]}
            events = inner.events()
        return "missed", {"layer": "hardened.orchestrator", "committed_step_ids": [event["step_id"] for event in events]}
    return probe


HARDENED_PROBES: dict[str, Probe] = {
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


def build_two_target_catalog() -> dict[str, Any]:
    if sorted(HARDENED_PROBES) != sorted(case.case_id for case in stock.CASES):
        raise ValueError("hardened probes do not cover exactly the stock cases")
    results = []
    for case in stock.CASES:
        stock_row = stock.run_case(case)
        classification, evidence = HARDENED_PROBES[case.case_id]()
        results.append({"case_id": case.case_id, "surface": case.surface, "description": case.description,
                        "stock": {"classification": stock_row["classification"], "evidence": stock_row["evidence"]},
                        "hardened": {"classification": classification, "evidence": evidence}})

    def count(target: str, verdict: str) -> int:
        return sum(row[target]["classification"] == verdict for row in results)

    return {"catalog_version": TWO_TARGET_VERSION,
            "targets": ["stock", "hardened"],
            "summary": {"cases": len(results),
                        "stock": {"caught": count("stock", "caught"), "missed": count("stock", "missed")},
                        "hardened": {"caught": count("hardened", "caught"), "missed": count("hardened", "missed")},
                        "flipped_to_caught": sorted(r["case_id"] for r in results
                                                    if r["stock"]["classification"] == "missed"
                                                    and r["hardened"]["classification"] == "caught"),
                        "regressed_to_missed": sorted(r["case_id"] for r in results
                                                      if r["stock"]["classification"] == "caught"
                                                      and r["hardened"]["classification"] == "missed")},
            "results": results}


def write_two_target_catalog(path: str | Path) -> dict[str, Any]:
    catalog = build_two_target_catalog()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(catalog, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return catalog
