"""Six-target adversarial catalog: the five earlier targets plus the anchored log.

The five earlier columns of the twenty-four earlier cases are
``five_target_catalog.build_five_target_catalog`` unchanged. The anchored column
replays every earlier case through ``AnchoredMemoryStore`` with the same probe
shapes; for the log cases the successor opens the tampered log with the anchor
the honest relay handed it. Two new cases test the anchor itself:

- ``anchor_stale_rollback``: the last line is dropped and the successor is
  handed the older anchor that matches the shorter log (a recorded miss: an
  anchor carries no freshness);
- ``anchor_forged_head``: the log is honest but the anchor handed forward
  carries a head seal that matches no prefix of it.

Earlier targets take no anchor, so they serve the log in both new cases.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Callable

from neural_node_swarm.lab import adversarial_catalog as stock
from neural_node_swarm.lab import five_target_catalog as five
from neural_node_swarm.lab import four_target_catalog as four
from neural_node_swarm.lab.anchored_log import Anchor, AnchoredMemoryStore
from neural_node_swarm.lab.hardened_store import HardenedCommitRejected
from neural_node_swarm.orchestrator import Orchestrator

SIX_TARGET_VERSION = "1.0"
EARLIER = list(five.TARGETS)
TARGETS = EARLIER + ["anchored"]

Probe = Callable[[], tuple[str, dict[str, Any]]]
AnchorPick = Callable[[Path, Anchor], Anchor]


def _held(path: Path, anchor: Anchor) -> Anchor:
    return anchor


def _stale(path: Path, anchor: Anchor) -> Anchor:
    lines = path.read_text(encoding="utf-8").splitlines()
    return Anchor(len(lines), json.loads(lines[-1])["seal"])


def _forged(path: Path, anchor: Anchor) -> Anchor:
    return Anchor(anchor.count, "f" * 64)


def _untouched(path: Path) -> None:
    return None


ANCHOR_CASES: dict[str, str] = {
    "anchor_stale_rollback": "The last line is dropped and the successor is handed the older matching anchor.",
    "anchor_forged_head": "The log is honest; the anchor handed forward has a head matching no prefix.",
}
ANCHOR_TAMPERS: dict[str, tuple[Callable[[Path], None], AnchorPick]] = {
    "anchor_stale_rollback": (five.tamper_truncate, _stale),
    "anchor_forged_head": (_untouched, _forged),
}
LOG_TAMPERS: dict[str, tuple[Callable[[Path], None], AnchorPick]] = {
    **{case_id: (tamper, _held) for case_id, tamper in five.TAMPERS.items()},
    **ANCHOR_TAMPERS,
}


def _anchored_log_probe(case_id: str) -> tuple[str, dict[str, Any]]:
    tamper, pick = LOG_TAMPERS[case_id]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "events.jsonl"
        relay = AnchoredMemoryStore(path)
        five.honest_relay(relay)
        tamper(path)
        try:
            store = AnchoredMemoryStore(path, anchor=pick(path, relay.anchor))
            events = store.events()
            latest = store.read_refs(["episodic:latest"])
        except ValueError as error:
            return "caught", {"layer": "anchored.read", "reason": getattr(error, "reason", type(error).__name__)}
        return "missed", {"layer": "anchored.read",
                          "events_served": len(events),
                          "served_step_ids": [event["step_id"] for event in events],
                          "latest_step_id": latest[0]["step_id"] if latest else None,
                          "forged_objective_served": any(event["output"].get("objective", "").startswith("FORGED")
                                                         for event in events)}


def _earlier_anchor_probe(target: str, case_id: str) -> tuple[str, dict[str, Any]]:
    tamper, _ = ANCHOR_TAMPERS[case_id]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "events.jsonl"
        five.honest_relay(five.STORES[target](path))
        tamper(path)
        store = five.STORES[target](path)
        try:
            events = store.events()
        except ValueError as error:
            return "caught", {"layer": f"{target}.read", "reason": getattr(error, "reason", type(error).__name__)}
        return "missed", {"layer": f"{target}.read", "anchor_checked": False,
                          "served_step_ids": [event["step_id"] for event in events]}


def _append_probe(build: Callable[[], Any]) -> Probe:
    def probe() -> tuple[str, dict[str, Any]]:
        with tempfile.TemporaryDirectory() as tmp:
            store = AnchoredMemoryStore(Path(tmp) / "events.jsonl")
            try:
                store.append(node_id="node-1", round=3, objective="seed", output=build())
            except HardenedCommitRejected as error:
                return "caught", {"layer": "anchored.append", "reason": error.reason,
                                  "events_committed": len(store.events()), "anchor_count": store.anchor.count}
            return "missed", {"layer": "anchored.append", "events_committed": len(store.events()),
                              "anchor_count": store.anchor.count}
    return probe


def _refs_probe(refs: list[str]) -> Probe:
    def probe() -> tuple[str, dict[str, Any]]:
        with tempfile.TemporaryDirectory() as tmp:
            store = AnchoredMemoryStore(Path(tmp) / "events.jsonl")
            store.append(node_id="node-0", round=3, objective="seed", output=stock._base(step_id="node-0:3"))
            try:
                resolved = store.read_refs(refs)
            except KeyError:
                return "caught", {"layer": "anchored.read_refs", "read": "KeyError"}
            except HardenedCommitRejected as error:
                return "caught", {"layer": "anchored.read_refs", "reason": error.reason}
        return "missed", {"layer": "anchored.read_refs", "refs_requested": len(refs),
                          "events_resolved": len(resolved), "distinct_events_in_store": 1}
    return probe


def _orchestrator_probe(factory: Callable[[dict[str, Any], int], Any]) -> Probe:
    def probe() -> tuple[str, dict[str, Any]]:
        with tempfile.TemporaryDirectory() as tmp:
            store = AnchoredMemoryStore(Path(tmp) / "events.jsonl")
            try:
                Orchestrator(store, factory).run("Seed objective", node_id="node-1")
            except HardenedCommitRejected as error:
                return "caught", {"layer": "anchored.orchestrator", "reason": error.reason,
                                  "committed_step_ids": [event["step_id"] for event in store.events()]}
            events = store.events()
        return "missed", {"layer": "anchored.orchestrator",
                          "committed_step_ids": [event["step_id"] for event in events]}
    return probe


ANCHORED_PROBES: dict[str, Probe] = {
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
    **{case_id: (lambda case_id=case_id: _anchored_log_probe(case_id)) for case_id in LOG_TAMPERS},
}


def build_six_target_catalog() -> dict[str, Any]:
    previous = five.build_five_target_catalog()
    expected = sorted([row["case_id"] for row in previous["results"]] + list(ANCHOR_CASES))
    if sorted(ANCHORED_PROBES) != expected:
        raise ValueError("anchored probes do not cover exactly the catalog cases")
    results = []
    for row in previous["results"]:
        classification, evidence = ANCHORED_PROBES[row["case_id"]]()
        results.append(dict(row, anchored={"classification": classification, "evidence": evidence}))
    for case_id, description in ANCHOR_CASES.items():
        row: dict[str, Any] = {"case_id": case_id, "surface": "anchor", "description": description}
        for target in EARLIER:
            classification, evidence = _earlier_anchor_probe(target, case_id)
            row[target] = {"classification": classification, "evidence": evidence}
        classification, evidence = ANCHORED_PROBES[case_id]()
        row["anchored"] = {"classification": classification, "evidence": evidence}
        results.append(row)

    def count(target: str, verdict: str) -> int:
        return sum(r[target]["classification"] == verdict for r in results)

    def moved(before: str, after: str) -> list[str]:
        return sorted(r["case_id"] for r in results
                      if r["sealed"]["classification"] == before and r["anchored"]["classification"] == after)

    return {"catalog_version": SIX_TARGET_VERSION,
            "targets": TARGETS,
            "ref_binding_policy": previous["ref_binding_policy"],
            "sealed_policy": previous["sealed_policy"],
            "anchored_policy": {"anchor": "(event count, seal of the last event)", "held": "outside events.jsonl",
                                "refuses": ["anchor_rollback", "anchor_head_mismatch", "anchor_malformed"],
                                "verified_on": ["events", "read_refs", "append"]},
            "summary": {"cases": len(results),
                        **{t: {"caught": count(t, "caught"), "missed": count(t, "missed")} for t in TARGETS},
                        "anchored_flipped_from_sealed": moved("missed", "caught"),
                        "anchored_regressed_from_sealed": moved("caught", "missed"),
                        "anchored_still_missed": sorted(r["case_id"] for r in results
                                                        if r["anchored"]["classification"] == "missed")},
            "residual_channels": previous["residual_channels"],
            "results": results}


def write_six_target_catalog(path: str | Path) -> dict[str, Any]:
    catalog = build_six_target_catalog()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(catalog, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return catalog
