"""Eight-target adversarial catalog: the seven earlier targets plus the witnessed log.

The seven earlier columns of the twenty-seven earlier cases are
``seven_target_catalog.build_seven_target_catalog`` unchanged. The witnessed
column replays every earlier case through ``WitnessedMemoryStore`` with the
same probe shapes; for the log cases the successor opens the tampered log with
the anchor the seven-target catalog hands it, and the witness file the honest
relay left beside the log. One new case tests the witness itself:

- ``witness_rollback``: the last line is dropped, the witness file is rolled
  back to the matching older anchor, and the successor is handed that anchor
  (a recorded miss: whoever can rewrite the witness file can rewind freshness).

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
from neural_node_swarm.lab import five_target_catalog as five
from neural_node_swarm.lab import four_target_catalog as four
from neural_node_swarm.lab import six_target_catalog as six
from neural_node_swarm.lab import seven_target_catalog as seven
from neural_node_swarm.lab.anchored_log import AnchoredMemoryStore
from neural_node_swarm.lab.bounded_log import BoundedMemoryStore
from neural_node_swarm.lab.hardened_store import HardenedCommitRejected
from neural_node_swarm.lab.witnessed_log import WitnessedMemoryStore, witness_path_for
from neural_node_swarm.orchestrator import Orchestrator

EIGHT_TARGET_VERSION = "1.0"
EARLIER = list(seven.TARGETS)
TARGETS = EARLIER + ["witnessed"]
ANCHORED_STORES: dict[str, Any] = {"anchored": AnchoredMemoryStore, "bounded": BoundedMemoryStore}

Probe = Callable[[], tuple[str, dict[str, Any]]]


def tamper_witness_rollback(path: Path) -> None:
    """Drop the last line and, if a witness file exists, rewind it to match."""
    five.tamper_truncate(path)
    witness = witness_path_for(path)
    if witness.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
        witness.write_text(json.dumps({"count": len(lines), "head": json.loads(lines[-1])["seal"]},
                                      sort_keys=True) + "\n", encoding="utf-8")


WITNESSED_CASES: dict[str, str] = {
    "witness_rollback": "The last line is dropped and the witness file is rolled back with it.",
}
LOG_TAMPERS: dict[str, tuple[Callable[[Path], None], six.AnchorPick]] = {
    **seven.LOG_TAMPERS,
    "witness_rollback": (tamper_witness_rollback, six._stale),
}


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


def _earlier_rollback_probe(target: str) -> tuple[str, dict[str, Any]]:
    if target in ANCHORED_STORES:
        return _log_probe(ANCHORED_STORES[target], f"{target}.read", "witness_rollback")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "events.jsonl"
        five.honest_relay(five.STORES[target](path))
        honest = five.STORES[target](path).events()
        tamper_witness_rollback(path)
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
            store = WitnessedMemoryStore(Path(tmp) / "events.jsonl")
            try:
                store.append(node_id="node-1", round=3, objective="seed", output=build())
            except HardenedCommitRejected as error:
                return "caught", {"layer": "witnessed.append", "reason": error.reason,
                                  "events_committed": len(store.events()),
                                  "witness_count": store.witness.current().count}
            return "missed", {"layer": "witnessed.append", "events_committed": len(store.events()),
                              "witness_count": store.witness.current().count}
    return probe


def _refs_probe(refs: list[str]) -> Probe:
    def probe() -> tuple[str, dict[str, Any]]:
        with tempfile.TemporaryDirectory() as tmp:
            store = WitnessedMemoryStore(Path(tmp) / "events.jsonl")
            store.append(node_id="node-0", round=3, objective="seed", output=stock._base(step_id="node-0:3"))
            try:
                resolved = store.read_refs(refs)
            except KeyError:
                return "caught", {"layer": "witnessed.read_refs", "read": "KeyError"}
            except HardenedCommitRejected as error:
                return "caught", {"layer": "witnessed.read_refs", "reason": error.reason}
        return "missed", {"layer": "witnessed.read_refs", "refs_requested": len(refs),
                          "events_resolved": len(resolved), "distinct_events_in_store": 1}
    return probe


def _orchestrator_probe(factory: Callable[[dict[str, Any], int], Any]) -> Probe:
    def probe() -> tuple[str, dict[str, Any]]:
        with tempfile.TemporaryDirectory() as tmp:
            store = WitnessedMemoryStore(Path(tmp) / "events.jsonl")
            try:
                Orchestrator(store, factory).run("Seed objective", node_id="node-1")
            except HardenedCommitRejected as error:
                return "caught", {"layer": "witnessed.orchestrator", "reason": error.reason,
                                  "committed_step_ids": [event["step_id"] for event in store.events()]}
            events = store.events()
        return "missed", {"layer": "witnessed.orchestrator",
                          "committed_step_ids": [event["step_id"] for event in events]}
    return probe


WITNESSED_PROBES: dict[str, Probe] = {
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
    **{case_id: (lambda case_id=case_id: _log_probe(WitnessedMemoryStore, "witnessed.read", case_id))
       for case_id in LOG_TAMPERS},
}


def build_eight_target_catalog() -> dict[str, Any]:
    previous = seven.build_seven_target_catalog()
    expected = sorted([row["case_id"] for row in previous["results"]] + list(WITNESSED_CASES))
    if sorted(WITNESSED_PROBES) != expected:
        raise ValueError("witnessed probes do not cover exactly the catalog cases")
    results = []
    for row in previous["results"]:
        classification, evidence = WITNESSED_PROBES[row["case_id"]]()
        results.append(dict(row, witnessed={"classification": classification, "evidence": evidence}))
    for case_id, description in WITNESSED_CASES.items():
        row: dict[str, Any] = {"case_id": case_id, "surface": "witness", "description": description}
        for target in EARLIER:
            classification, evidence = _earlier_rollback_probe(target)
            row[target] = {"classification": classification, "evidence": evidence}
        classification, evidence = WITNESSED_PROBES[case_id]()
        row["witnessed"] = {"classification": classification, "evidence": evidence}
        results.append(row)

    def count(target: str, verdict: str) -> int:
        return sum(r[target]["classification"] == verdict for r in results)

    def moved(before: str, after: str) -> list[str]:
        return sorted(r["case_id"] for r in results
                      if r["bounded"]["classification"] == before and r["witnessed"]["classification"] == after)

    return {"catalog_version": EIGHT_TARGET_VERSION,
            "targets": TARGETS,
            "ref_binding_policy": previous["ref_binding_policy"],
            "sealed_policy": previous["sealed_policy"],
            "anchored_policy": previous["anchored_policy"],
            "bounded_policy": previous["bounded_policy"],
            "witnessed_policy": {"witness": "highest anchor ever issued, in its own file beside events.jsonl",
                                 "refuses": ["anchor_stale", "anchor_witness_fork", "witness_regress",
                                             "witness_fork", "witness_malformed"],
                                 "advances": "on each of the store's own appends, or to a verified caller "
                                             "anchor ahead of it"},
            "summary": {"cases": len(results),
                        **{t: {"caught": count(t, "caught"), "missed": count(t, "missed")} for t in TARGETS},
                        "witnessed_flipped_from_bounded": moved("missed", "caught"),
                        "witnessed_regressed_from_bounded": moved("caught", "missed"),
                        "witnessed_still_missed": sorted(r["case_id"] for r in results
                                                         if r["witnessed"]["classification"] == "missed")},
            "residual_channels": previous["residual_channels"],
            "results": results}


def write_eight_target_catalog(path: str | Path) -> dict[str, Any]:
    catalog = build_eight_target_catalog()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(catalog, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return catalog
