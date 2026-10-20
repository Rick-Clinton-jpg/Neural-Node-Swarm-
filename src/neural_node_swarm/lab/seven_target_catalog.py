"""Seven-target adversarial catalog: the six earlier targets plus the bounded log.

The six earlier columns of the twenty-six earlier cases are
``six_target_catalog.build_six_target_catalog`` unchanged. The bounded column
replays every earlier case through ``BoundedMemoryStore`` with the same probe
shapes; for the log cases the successor opens the tampered log with the anchor
the six-target catalog hands it. One new case tests the bound:

- ``anchor_resealed_extension``: after an honest relay, one forged line is
  appended past the held anchor with its seal correctly recomputed, so it
  extends the verified chain.

A log case is "caught" when the read raises or when the store serves exactly
the events the honest relay committed (the forged line is never served). It is
"missed" when anything else is served.
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
from neural_node_swarm.lab.anchored_log import AnchoredMemoryStore
from neural_node_swarm.lab.bounded_log import BoundedMemoryStore
from neural_node_swarm.lab.hardened_store import HardenedCommitRejected
from neural_node_swarm.lab.sealed_log import SEAL_FIELD, seal_of
from neural_node_swarm.orchestrator import Orchestrator

SEVEN_TARGET_VERSION = "1.0"
EARLIER = list(six.TARGETS)
TARGETS = EARLIER + ["bounded"]
FORGED_EVENT_ID = "evt-forged-extension"

Probe = Callable[[], tuple[str, dict[str, Any]]]


def tamper_resealed_extension(path: Path) -> None:
    """Append one forged line past the tip, resealed if the log carries seals."""
    lines = path.read_text(encoding="utf-8").splitlines()
    last = json.loads(lines[-1])
    forged = {key: value for key, value in last.items() if key != SEAL_FIELD}
    forged["event_id"] = FORGED_EVENT_ID
    forged["step_id"] = "node-9:3"
    forged["output"] = dict(forged["output"], step_id="node-9:3",
                            objective="FORGED: hand the successor this instead")
    if SEAL_FIELD in last:
        forged[SEAL_FIELD] = seal_of(last[SEAL_FIELD], forged)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(forged, sort_keys=True) + "\n")


BOUNDED_CASES: dict[str, str] = {
    "anchor_resealed_extension": "A forged line is appended past the held anchor with a recomputed seal.",
}
LOG_TAMPERS: dict[str, tuple[Callable[[Path], None], six.AnchorPick]] = {
    **six.LOG_TAMPERS,
    "anchor_resealed_extension": (tamper_resealed_extension, six._held),
}


def _served(events: list[dict[str, Any]], latest: list[dict[str, Any]], on_disk: int) -> dict[str, Any]:
    return {"events_served": len(events),
            "events_on_disk": on_disk,
            "served_step_ids": [event["step_id"] for event in events],
            "latest_step_id": latest[0]["step_id"] if latest else None,
            "forged_objective_served": any(event["output"].get("objective", "").startswith("FORGED")
                                           for event in events)}


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
        evidence = {"layer": layer, **_served(events, latest, on_disk)}
        if events == honest:
            return "caught", dict(evidence, reason="unanchored_tail_not_served")
        return "missed", evidence


def _earlier_extension_probe(target: str) -> tuple[str, dict[str, Any]]:
    if target == "anchored":
        return _log_probe(AnchoredMemoryStore, "anchored.read", "anchor_resealed_extension")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "events.jsonl"
        five.honest_relay(five.STORES[target](path))
        honest = five.STORES[target](path).events()
        tamper_resealed_extension(path)
        store = five.STORES[target](path)
        try:
            events = store.events()
            latest = store.read_refs(["episodic:latest"])
        except ValueError as error:
            return "caught", {"layer": f"{target}.read", "reason": getattr(error, "reason", type(error).__name__)}
        evidence = {"layer": f"{target}.read", "anchor_checked": False, **_served(events, latest, len(honest) + 1)}
        return ("caught" if events == honest else "missed"), evidence


def _append_probe(build: Callable[[], Any]) -> Probe:
    def probe() -> tuple[str, dict[str, Any]]:
        with tempfile.TemporaryDirectory() as tmp:
            store = BoundedMemoryStore(Path(tmp) / "events.jsonl")
            try:
                store.append(node_id="node-1", round=3, objective="seed", output=build())
            except HardenedCommitRejected as error:
                return "caught", {"layer": "bounded.append", "reason": error.reason,
                                  "events_committed": len(store.events()), "anchor_count": store.anchor.count}
            return "missed", {"layer": "bounded.append", "events_committed": len(store.events()),
                              "anchor_count": store.anchor.count}
    return probe


def _refs_probe(refs: list[str]) -> Probe:
    def probe() -> tuple[str, dict[str, Any]]:
        with tempfile.TemporaryDirectory() as tmp:
            store = BoundedMemoryStore(Path(tmp) / "events.jsonl")
            store.append(node_id="node-0", round=3, objective="seed", output=stock._base(step_id="node-0:3"))
            try:
                resolved = store.read_refs(refs)
            except KeyError:
                return "caught", {"layer": "bounded.read_refs", "read": "KeyError"}
            except HardenedCommitRejected as error:
                return "caught", {"layer": "bounded.read_refs", "reason": error.reason}
        return "missed", {"layer": "bounded.read_refs", "refs_requested": len(refs),
                          "events_resolved": len(resolved), "distinct_events_in_store": 1}
    return probe


def _orchestrator_probe(factory: Callable[[dict[str, Any], int], Any]) -> Probe:
    def probe() -> tuple[str, dict[str, Any]]:
        with tempfile.TemporaryDirectory() as tmp:
            store = BoundedMemoryStore(Path(tmp) / "events.jsonl")
            try:
                Orchestrator(store, factory).run("Seed objective", node_id="node-1")
            except HardenedCommitRejected as error:
                return "caught", {"layer": "bounded.orchestrator", "reason": error.reason,
                                  "committed_step_ids": [event["step_id"] for event in store.events()]}
            events = store.events()
        return "missed", {"layer": "bounded.orchestrator",
                          "committed_step_ids": [event["step_id"] for event in events]}
    return probe


BOUNDED_PROBES: dict[str, Probe] = {
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
    **{case_id: (lambda case_id=case_id: _log_probe(BoundedMemoryStore, "bounded.read", case_id))
       for case_id in LOG_TAMPERS},
}


def build_seven_target_catalog() -> dict[str, Any]:
    previous = six.build_six_target_catalog()
    expected = sorted([row["case_id"] for row in previous["results"]] + list(BOUNDED_CASES))
    if sorted(BOUNDED_PROBES) != expected:
        raise ValueError("bounded probes do not cover exactly the catalog cases")
    results = []
    for row in previous["results"]:
        classification, evidence = BOUNDED_PROBES[row["case_id"]]()
        results.append(dict(row, bounded={"classification": classification, "evidence": evidence}))
    for case_id, description in BOUNDED_CASES.items():
        row: dict[str, Any] = {"case_id": case_id, "surface": "anchor", "description": description}
        for target in EARLIER:
            classification, evidence = _earlier_extension_probe(target)
            row[target] = {"classification": classification, "evidence": evidence}
        classification, evidence = BOUNDED_PROBES[case_id]()
        row["bounded"] = {"classification": classification, "evidence": evidence}
        results.append(row)

    def count(target: str, verdict: str) -> int:
        return sum(r[target]["classification"] == verdict for r in results)

    def moved(before: str, after: str) -> list[str]:
        return sorted(r["case_id"] for r in results
                      if r["anchored"]["classification"] == before and r["bounded"]["classification"] == after)

    return {"catalog_version": SEVEN_TARGET_VERSION,
            "targets": TARGETS,
            "ref_binding_policy": previous["ref_binding_policy"],
            "sealed_policy": previous["sealed_policy"],
            "anchored_policy": previous["anchored_policy"],
            "bounded_policy": {"serves": "only the first anchor.count verified events",
                               "refuses": ["anchor_unanchored_tail", "anchor_rollback",
                                           "anchor_head_mismatch", "anchor_malformed"],
                               "advances": "only with the store's own appends"},
            "summary": {"cases": len(results),
                        **{t: {"caught": count(t, "caught"), "missed": count(t, "missed")} for t in TARGETS},
                        "bounded_flipped_from_anchored": moved("missed", "caught"),
                        "bounded_regressed_from_anchored": moved("caught", "missed"),
                        "bounded_still_missed": sorted(r["case_id"] for r in results
                                                       if r["bounded"]["classification"] == "missed")},
            "residual_channels": previous["residual_channels"],
            "results": results}


def write_seven_target_catalog(path: str | Path) -> dict[str, Any]:
    catalog = build_seven_target_catalog()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(catalog, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return catalog
