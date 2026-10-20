"""Pins the shaped store and the ten-target adversarial catalog.

Every earlier target serves objective_reasoning_smuggle: a reasoning trace
packed into objective up to its 1000-char cap passes the verifier, the budget,
ref binding and every log layer. The shaped store refuses (objective_shape) an
objective longer than 400 chars, with more than 3 sentences, or whose
zlib-compressed size is under half its UTF-8 size, and records each measured
value as evidence. A short, non-repeating trace inside all three limits
(objective_paraphrased_trace) is a recorded miss on every target.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

from neural_node_swarm.lab.adversarial_catalog import _base, _objective_smuggle
from neural_node_swarm.lab.anchored_log import Anchor
from neural_node_swarm.lab.five_target_catalog import TAMPERS, honest_relay
from neural_node_swarm.lab.nine_target_catalog import build_nine_target_catalog
from neural_node_swarm.lab.objective_shape import (
    MAX_CHARS, MAX_SENTENCES, ObjectiveShapeRejected, ShapedMemoryStore, measure_objective, shape_violations)
from neural_node_swarm.lab.quorum_log import QuorumMemoryStore, replica_paths_for
from neural_node_swarm.lab.ten_target_catalog import (
    PARAPHRASED_TRACE, SHAPE_CASES, build_ten_target_catalog)
from neural_node_swarm.lab.witnessed_log import AnchorWitness

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs" / "lab" / "adversarial_catalog_ten_targets.json"
NINE_TARGET = ROOT / "outputs" / "lab" / "adversarial_catalog_nine_targets.json"
EARLIER = ["stock", "hardened", "budget", "ref_binding", "sealed", "anchored", "bounded", "witnessed", "quorum"]
HONEST = ["node-1:1", "node-1:2", "node-1:3"]


def _noise(chars: int) -> str:
    """Deterministic, incompressible-ish text with no sentence breaks."""
    raw = b"".join(hashlib.sha256(str(i).encode()).digest() for i in range(chars // 32 + 1))
    return base64.b64encode(raw).decode("ascii")[:chars]


def _ids(events):
    return [event["step_id"] for event in events]


def _refused(tmp_path: Path, objective: str) -> ObjectiveShapeRejected:
    store = ShapedMemoryStore(tmp_path / "events.jsonl")
    with pytest.raises(ObjectiveShapeRejected) as info:
        store.append(node_id="node-1", round=3, objective="seed", output=_base(objective=objective))
    assert info.value.reason == "objective_shape"
    assert store.events() == []
    assert all(AnchorWitness(p).current().count == 0 for p in replica_paths_for(tmp_path / "events.jsonl"))
    return info.value


def test_shaped_store_is_a_quorum_store():
    assert issubclass(ShapedMemoryStore, QuorumMemoryStore)
    assert (MAX_CHARS, MAX_SENTENCES) == (400, 3)


def test_measure_objective_reports_every_value():
    measured = measure_objective("Add one test. Then stop.")
    assert measured["chars"] == 24 and measured["utf8_bytes"] == 24
    assert measured["sentences"] == 2
    assert measured["compressed_bytes"] > 0
    assert shape_violations(measured) == []
    assert measure_objective("Pin v1.0 of the schema")["sentences"] == 1


def test_smuggled_trace_is_refused_with_every_measurement(tmp_path):
    error = _refused(tmp_path, _objective_smuggle()["objective"])
    assert set(error.violations) == {"chars", "sentences", "compression"}
    assert error.measured["chars"] == 1000
    assert error.measured["compressed_bytes"] * 2 < error.measured["utf8_bytes"]


@pytest.mark.parametrize("objective, violation", [
    (_noise(401), "chars"),
    ("One. Two. Three. Four.", "sentences"),
    ("ab" * 150, "compression"),
])
def test_each_limit_refuses_on_its_own(tmp_path, objective, violation):
    assert _refused(tmp_path, objective).violations == [violation]


def test_limits_are_inclusive(tmp_path):
    store = ShapedMemoryStore(tmp_path / "events.jsonl")
    for index, objective in enumerate(["One. Two. Three.", _noise(400)]):
        assert shape_violations(measure_objective(objective)) == []
        store.append(node_id="node-1", round=index + 1, objective="seed",
                     output=_base(step_id=f"node-1:{index + 1}", objective=objective))
    assert len(store.events()) == 2


def test_paraphrased_trace_is_within_every_limit_and_is_served(tmp_path):
    assert shape_violations(measure_objective(PARAPHRASED_TRACE)) == []
    store = ShapedMemoryStore(tmp_path / "events.jsonl")
    store.append(node_id="node-1", round=3, objective="seed", output=_base(objective=PARAPHRASED_TRACE))
    assert store.read_refs(["episodic:latest"])[0]["output"]["objective"] == PARAPHRASED_TRACE


def test_smuggle_is_missed_by_quorum_and_caught_by_shaped(tmp_path):
    quorum = QuorumMemoryStore(tmp_path / "q" / "events.jsonl")
    quorum.append(node_id="node-1", round=3, objective="seed", output=_objective_smuggle())
    assert len(quorum.events()) == 1
    shaped = ShapedMemoryStore(tmp_path / "s" / "events.jsonl")
    with pytest.raises(ObjectiveShapeRejected):
        shaped.append(node_id="node-1", round=3, objective="seed", output=_objective_smuggle())
    assert shaped.events() == []


def test_shape_runs_on_the_canonical_form_and_defers_other_rejections(tmp_path):
    store = ShapedMemoryStore(tmp_path / "events.jsonl")
    with pytest.raises(ValueError) as info:
        store.append(node_id="node-1", round=3, objective="seed", output=_base(objective="x" * 1001))
    assert info.value.reason == "objective_bytes"
    with pytest.raises(ValueError) as info:
        store.append(node_id="node-1", round=3, objective="seed", output=_base(confidence=float("nan")))
    assert info.value.reason != "objective_shape"


def test_honest_relay_passing_its_anchor_forward_commits_and_reads_back(tmp_path):
    path = tmp_path / "events.jsonl"
    store = ShapedMemoryStore(path)
    honest_relay(store)
    held = store.anchor
    assert held.count == 3
    successor = ShapedMemoryStore(path, anchor=held)
    assert _ids(successor.events()) == HONEST
    nxt = successor.append_anchored(node_id="node-2", round=1, objective="seed", output=_base(step_id="node-2:1"))
    assert nxt == Anchor(4, nxt.head)
    third = ShapedMemoryStore(path, anchor=nxt)
    assert third.read_refs(["episodic:latest"])[0]["step_id"] == "node-2:1"


@pytest.mark.parametrize("case_id", sorted(TAMPERS))
def test_every_earlier_log_tamper_stays_refused(tmp_path, case_id):
    path = tmp_path / "events.jsonl"
    store = ShapedMemoryStore(path)
    honest_relay(store)
    held = store.anchor
    TAMPERS[case_id](path)
    with pytest.raises(ValueError):
        ShapedMemoryStore(path, anchor=held).events()


def test_ten_targets_keep_the_nine_target_columns_unchanged():
    nine = json.loads(NINE_TARGET.read_text(encoding="utf-8"))
    ten = build_ten_target_catalog()
    old_rows = {row["case_id"]: row for row in nine["results"]}
    for row in ten["results"]:
        if row["case_id"] in old_rows:
            for target in nine["targets"]:
                assert row[target] == old_rows[row["case_id"]][target]
    assert set(old_rows) | set(SHAPE_CASES) == {row["case_id"] for row in ten["results"]}
    assert ten["targets"] == nine["targets"] + ["shaped"]
    assert nine == build_nine_target_catalog()


def test_shaped_flips_exactly_the_objective_smuggle_and_regresses_nothing():
    catalog = build_ten_target_catalog()
    summary = catalog["summary"]
    assert summary["cases"] == 31
    assert summary["shaped_flipped_from_quorum"] == ["objective_reasoning_smuggle"]
    assert summary["shaped_regressed_from_quorum"] == []
    assert summary["shaped_still_missed"] == ["objective_paraphrased_trace", "quorum_full_rollback"]
    rows = {row["case_id"]: row for row in catalog["results"]}
    evidence = rows["objective_reasoning_smuggle"]["shaped"]["evidence"]
    assert evidence["reason"] == "objective_shape"
    assert evidence["measured"]["chars"] == 1000
    assert sorted(evidence["violations"]) == ["chars", "compression", "sentences"]
    for target in EARLIER + ["shaped"]:
        assert rows["objective_paraphrased_trace"][target]["classification"] == "missed", target
    assert rows["objective_paraphrased_trace"]["shaped"]["evidence"]["violations"] == []


def test_ten_target_catalog_is_deterministic():
    assert build_ten_target_catalog() == build_ten_target_catalog()


def test_committed_ten_target_results_match_a_fresh_run():
    committed = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert committed == build_ten_target_catalog()
