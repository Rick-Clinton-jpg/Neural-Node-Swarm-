"""Nine-target adversarial catalog: the eight earlier targets plus the quorum log.

The eight earlier columns of the twenty-eight earlier cases are
``eight_target_catalog.build_eight_target_catalog`` unchanged. The quorum
column replays every earlier case through ``QuorumMemoryStore`` with the same
probe shapes; for the log cases the successor opens the tampered log with the
anchor the eight-target catalog hands it and the replicas the honest relay
left. Two new cases roll witnesses back together with the log:

- ``quorum_partial_rollback``: the last line is dropped and every witness
  replica but the last is rewound to the matching older anchor (caught: one
  untouched replica still holds the fresh anchor);
- ``quorum_full_rollback``: the last line is dropped and every replica is
  rewound (a recorded miss: whoever can rewrite every replica rewinds freshness).

A log case is "caught" when the read raises or when the store serves exactly
the events the honest relay committed. It is "missed" when anything else is
served.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Callable

from neural_node_swarm.lab import adversarial_catalog as stock
from neural_node_swarm.lab import eight_target_catalog as eight
from neural_node_swarm.lab import five_target_catalog as five
from neural_node_swarm.lab import four_target_catalog as four
from neural_node_swarm.lab import seven_target_catalog as seven
from neural_node_swarm.lab import six_target_catalog as six
from neural_node_swarm.lab.anchored_log import AnchoredMemoryStore
from neural_node_swarm.lab.bounded_log import BoundedMemoryStore
from neural_node_swarm.lab.hardened_store import HardenedCommitRejected
from neural_node_swarm.lab.quorum_log import DEFAULT_REPLICAS, QuorumMemoryStore, replica_paths_for
from neural_node_swarm.lab.witnessed_log import WitnessedMemoryStore
from neural_node_swarm.orchestrator import Orchestrator

NINE_TARGET_VERSION = "1.0"
EARLIER = list(eight.TARGETS)
TARGETS = EARLIER + ["quorum"]
ANCHORED_STORES: dict[str, Any] = {"anchored": AnchoredMemoryStore, "bounded": BoundedMemoryStore,
                                   "witnessed": WitnessedMemoryStore}

Probe = Callable[[], tuple[str, dict[str, Any]]]


def _rewind(path: Path, keep: int) -> None:
    """Drop the last line and rewind every existing replica except the last ``keep``."""
    five.tamper_truncate(path)
    replicas = [replica for replica in replica_paths_for(path)[:DEFAULT_REPLICAS - keep] if replica.exists()]
    if not replicas:
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    stale = json.dumps({"count": len(lines), "head": json.loads(lines[-1])["seal"]}, sort_keys=True) + "\n"
    for replica in replicas:
        replica.write_text(stale, encoding="utf-8")


def tamper_quorum_partial_rollback(path: Path) -> None:
    _rewind(path, keep=1)


def tamper_quorum_full_rollback(path: Path) -> None:
    _rewind(path, keep=0)


QUORUM_CASES: dict[str, str] = {
    "quorum_partial_rollback": "The last line is dropped and every witness replica but one is rolled back with it.",
    "quorum_full_rollback": "The last line is dropped and every witness replica is rolled back with it.",
}
NEW_TAMPERS: dict[str, tuple[Callable[[Path], None], six.AnchorPick]] = {
    "quorum_partial_rollback": (tamper_quorum_partial_rollback, six._stale),
    "quorum_full_rollback": (tamper_quorum_full_rollback, six._stale),
}
LOG_TAMPERS: dict[str, tuple[Callable[[Path], None], six.AnchorPick]] = {**eight.LOG_TAMPERS, **NEW_TAMPERS}


def _log_probe(store_class: Any, layer: str, case_id: str) -> tuple[str, dict[str, Any]]:
    tamper, pick = LOG_TAMPERS[case_id]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "events.jsonl"
        relay = store_class(path)
        five.honest_relay(relay)
        honest = relay.events()
        tamper(path)
        on_disk = len(path.read_text(encoding="utf-8").splitlines())
        try:
            store = store_class(path, anchor=pick(path, relay.anchor))
            events = store.events()
            latest = store.read_refs(["episodic:latest"])
        except ValueError as error:
            return "caught", {"layer": layer, "reason": getattr(error, "reason", type(error).__name__)}
        evidence = {"layer": layer, **seven._served(events, latest, on_disk)}
        if events == honest:
            return "caught", dict(evidence, reason="unanchored_tail_not_served")
        return "missed", evidence


def _earlier_new_case_probe(target: str, case_id: str) -> tuple[str, dict[str, Any]]:
    if target in ANCHORED_STORES:
        return _log_probe(ANCHORED_STORES[target], f"{target}.read", case_id)
    tamper, _ = NEW_TAMPERS[case_id]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "events.jsonl"
        five.honest_relay(five.STORES[target](path))
        honest = five.STORES[target](path).events()
        tamper(path)
        store = five.STORES[target](path)
        try:
            events = store.events()
            latest = store.read_refs(["episodic:latest"])
        except ValueError as error:
            return "caught", {"layer": f"{target}.read", "reason": getattr(error, "reason", type(error).__name__)}
        evidence = {"layer": f"{target}.read", "anchor_checked": False,
                    **seven._served(events, latest, len(honest) - 1)}
        return ("caught" if events == honest else "missed"), evidence


def _append_probe(build: Callable[[], Any]) -> Probe:
    def probe() -> tuple[str, dict[str, Any]]:
        with tempfile.TemporaryDirectory() as tmp:
            store = QuorumMemoryStore(Path(tmp) / "events.jsonl")
            try:
                store.append(node_id="node-1", round=3, objective="seed", output=build())
            except HardenedCommitRejected as error:
                return "caught", {"layer": "quorum.append", "reason": error.reason,
                                  "events_committed": len(store.events()),
                                  "witness_counts": [a.count for a in store.witness.anchors()]}
            return "missed", {"layer": "quorum.append", "events_committed": len(store.events()),
                              "witness_counts": [a.count for a in store.witness.anchors()]}
    return probe


def _refs_probe(refs: list[str]) -> Probe:
    def probe() -> tuple[str, dict[str, Any]]:
        with tempfile.TemporaryDirectory() as tmp:
            store = QuorumMemoryStore(Path(tmp) / "events.jsonl")
            store.append(node_id="node-0", round=3, objective="seed", output=stock._base(step_id="node-0:3"))
            try:
                resolved = store.read_refs(refs)
            except KeyError:
                return "caught", {"layer": "quorum.read_refs", "read": "KeyError"}
            except HardenedCommitRejected as error:
                return "caught", {"layer": "quorum.read_refs", "reason": error.reason}
        return "missed", {"layer": "quorum.read_refs", "refs_requested": len(refs),
                          "events_resolved": len(resolved), "distinct_events_in_store": 1}
    return probe


def _orchestrator_probe(factory: Callable[[dict[str, Any], int], Any]) -> Probe:
    def probe() -> tuple[str, dict[str, Any]]:
        with tempfile.TemporaryDirectory() as tmp:
            store = QuorumMemoryStore(Path(tmp) / "events.jsonl")
            try:
                Orchestrator(store, factory).run("Seed objective", node_id="node-1")
            except HardenedCommitRejected as error:
                return "caught", {"layer": "quorum.orchestrator", "reason": error.reason,
                                  "committed_step_ids": [event["step_id"] for event in store.events()]}
            events = store.events()
        return "missed", {"layer": "quorum.orchestrator",
                          "committed_step_ids": [event["step_id"] for event in events]}
    return probe


QUORUM_PROBES: dict[str, Probe] = {
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
    "memory_ref_payload_channel": _append_probe(four.NEW_CASES["memory_ref_payload_channel"][2]),
    "confidence_mantissa_channel": _append_probe(four.NEW_CASES["confidence_mantissa_channel"][2]),
    **{case_id: (lambda case_id=case_id: _log_probe(QuorumMemoryStore, "quorum.read", case_id))
       for case_id in LOG_TAMPERS},
}


def build_nine_target_catalog() -> dict[str, Any]:
    previous = eight.build_eight_target_catalog()
    expected = sorted([row["case_id"] for row in previous["results"]] + list(QUORUM_CASES))
    if sorted(QUORUM_PROBES) != expected:
        raise ValueError("quorum probes do not cover exactly the catalog cases")
    results = []
    for row in previous["results"]:
        classification, evidence = QUORUM_PROBES[row["case_id"]]()
        results.append(dict(row, quorum={"classification": classification, "evidence": evidence}))
    for case_id, description in QUORUM_CASES.items():
        row: dict[str, Any] = {"case_id": case_id, "surface": "witness", "description": description}
        for target in EARLIER:
            classification, evidence = _earlier_new_case_probe(target, case_id)
            row[target] = {"classification": classification, "evidence": evidence}
        classification, evidence = QUORUM_PROBES[case_id]()
        row["quorum"] = {"classification": classification, "evidence": evidence}
        results.append(row)

    def count(target: str, verdict: str) -> int:
        return sum(r[target]["classification"] == verdict for r in results)

    def moved(before: str, after: str) -> list[str]:
        return sorted(r["case_id"] for r in results
                      if r["witnessed"]["classification"] == before and r["quorum"]["classification"] == after)

    return {"catalog_version": NINE_TARGET_VERSION,
            "targets": TARGETS,
            "ref_binding_policy": previous["ref_binding_policy"],
            "sealed_policy": previous["sealed_policy"],
            "anchored_policy": previous["anchored_policy"],
            "bounded_policy": previous["bounded_policy"],
            "witnessed_policy": previous["witnessed_policy"],
            "quorum_policy": {"witness": "highest anchor held by any of N replicas in separate directories",
                              "replicas": len(replica_paths_for("events.jsonl")),
                              "refuses": ["anchor_stale", "witness_fork", "witness_regress",
                                          "witness_malformed"],
                              "advances": "every replica on each of the store's own appends"},
            "summary": {"cases": len(results),
                        **{t: {"caught": count(t, "caught"), "missed": count(t, "missed")} for t in TARGETS},
                        "quorum_flipped_from_witnessed": moved("missed", "caught"),
                        "quorum_regressed_from_witnessed": moved("caught", "missed"),
                        "quorum_still_missed": sorted(r["case_id"] for r in results
                                                      if r["quorum"]["classification"] == "missed")},
            "residual_channels": previous["residual_channels"],
            "results": results}


def write_nine_target_catalog(path: str | Path) -> dict[str, Any]:
    catalog = build_nine_target_catalog()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(catalog, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return catalog
