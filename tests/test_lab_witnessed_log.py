"""Pins the witnessed log and the eight-target adversarial catalog.

A bounded store accepts any anchor that the on-disk log extends, so a dropped
tail handed forward with the older matching anchor (anchor_stale_rollback) is
served. The witnessed store keeps an AnchorWitness in its own file outside
events.jsonl recording the highest anchor ever issued, refuses (anchor_stale)
any caller anchor behind it, and advances it on every append. Rolling the
witness file back together with the log (witness_rollback) is a recorded miss:
trust moved to the witness file, it did not vanish.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from neural_node_swarm.lab.adversarial_catalog import _base
from neural_node_swarm.lab.anchored_log import Anchor, AnchorRefused
from neural_node_swarm.lab.bounded_log import BoundedMemoryStore
from neural_node_swarm.lab.eight_target_catalog import (
    WITNESSED_CASES, build_eight_target_catalog, tamper_witness_rollback)
from neural_node_swarm.lab.five_target_catalog import TAMPERS, honest_relay
from neural_node_swarm.lab.sealed_log import GENESIS
from neural_node_swarm.lab.seven_target_catalog import build_seven_target_catalog, tamper_resealed_extension
from neural_node_swarm.lab.witnessed_log import AnchorWitness, WitnessedMemoryStore, witness_path_for

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs" / "lab" / "adversarial_catalog_eight_targets.json"
SEVEN_TARGET = ROOT / "outputs" / "lab" / "adversarial_catalog_seven_targets.json"
EARLIER = ["stock", "hardened", "budget", "ref_binding", "sealed", "anchored", "bounded"]
HONEST = ["node-1:1", "node-1:2", "node-1:3"]


def _relay(path: Path) -> Anchor:
    store = WitnessedMemoryStore(path)
    honest_relay(store)
    return store.anchor


def _ids(events):
    return [event["step_id"] for event in events]


def _stale_anchor(path: Path) -> Anchor:
    lines = path.read_text(encoding="utf-8").splitlines()
    return Anchor(len(lines), json.loads(lines[-1])["seal"])


def test_witnessed_store_is_a_bounded_store_and_the_witness_lives_outside_the_log(tmp_path):
    assert issubclass(WitnessedMemoryStore, BoundedMemoryStore)
    path = tmp_path / "events.jsonl"
    held = _relay(path)
    assert witness_path_for(path) != path and witness_path_for(path).parent == path.parent
    assert AnchorWitness(witness_path_for(path)).current() == held
    assert all("witness" not in line for line in path.read_text(encoding="utf-8").splitlines())


def test_witness_refuses_a_lower_count_or_a_fork_and_advances_otherwise(tmp_path):
    witness = AnchorWitness(tmp_path / "w.json")
    assert witness.current() == Anchor(0, GENESIS)
    witness.record(Anchor(2, "a" * 64))
    witness.record(Anchor(2, "a" * 64))  # same anchor again is fine
    for anchor, reason in ((Anchor(1, "a" * 64), "witness_regress"),
                           (Anchor(2, "b" * 64), "witness_fork")):
        with pytest.raises(AnchorRefused) as info:
            witness.record(anchor)
        assert info.value.reason == reason
    assert witness.current() == Anchor(2, "a" * 64)
    witness.record(Anchor(3, "c" * 64))
    assert witness.current() == Anchor(3, "c" * 64)


@pytest.mark.parametrize("content", ["not json", "[]", '{"count": -1, "head": "' + "a" * 64 + '"}',
                                     '{"count": true, "head": "' + "a" * 64 + '"}', '{"count": 1, "head": "zz"}'])
def test_malformed_witness_is_refused(tmp_path, content):
    path = tmp_path / "w.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(AnchorRefused) as info:
        AnchorWitness(path).current()
    assert info.value.reason == "witness_malformed"


def test_stale_rollback_is_missed_by_bounded_and_caught_by_witnessed(tmp_path):
    path = tmp_path / "events.jsonl"
    _relay(path)
    TAMPERS["log_tail_truncation"](path)
    stale = _stale_anchor(path)
    assert _ids(BoundedMemoryStore(path, anchor=stale).events()) == HONEST[:2]
    with pytest.raises(AnchorRefused) as info:
        WitnessedMemoryStore(path, anchor=stale)
    assert info.value.reason == "anchor_stale"
    with pytest.raises(AnchorRefused):
        WitnessedMemoryStore(path)  # adopting the shorter tip is just as stale


def test_an_equal_count_with_a_different_head_is_refused(tmp_path):
    path = tmp_path / "events.jsonl"
    held = _relay(path)
    with pytest.raises(ValueError):
        WitnessedMemoryStore(path, anchor=Anchor(held.count, "f" * 64))


def test_an_older_anchor_is_refused_rather_than_served_a_prefix(tmp_path):
    path = tmp_path / "events.jsonl"
    store = WitnessedMemoryStore(path)
    store.append(node_id="node-0", round=3, objective="seed", output=_base(step_id="node-0:3"))
    older = store.anchor
    honest_relay(store)
    assert _ids(BoundedMemoryStore(path, anchor=older).events()) == ["node-0:3"]
    with pytest.raises(AnchorRefused) as info:
        WitnessedMemoryStore(path, anchor=older)
    assert info.value.reason == "anchor_stale"


def test_a_store_left_behind_by_another_writer_stops_serving(tmp_path):
    path = tmp_path / "events.jsonl"
    held = _relay(path)
    left_behind = WitnessedMemoryStore(path, anchor=held)
    writer = WitnessedMemoryStore(path, anchor=held)
    writer.append(node_id="node-2", round=1, objective="seed", output=_base(step_id="node-2:1"))
    with pytest.raises(AnchorRefused) as info:
        left_behind.events()
    assert info.value.reason == "anchor_stale"


def test_honest_relay_passing_its_anchor_forward_commits_and_reads_back(tmp_path):
    path = tmp_path / "events.jsonl"
    store = WitnessedMemoryStore(path)
    assert store.anchor == Anchor(0, GENESIS)
    honest_relay(store)
    held = store.anchor
    assert held.count == 3 and store.witness.current() == held
    successor = WitnessedMemoryStore(path, anchor=held)
    assert _ids(successor.events()) == HONEST
    nxt = successor.append_anchored(node_id="node-2", round=1, objective="seed", output=_base(step_id="node-2:1"))
    assert nxt.count == 4 and successor.witness.current() == nxt
    third = WitnessedMemoryStore(path, anchor=nxt)
    assert _ids(third.events()) == HONEST + ["node-2:1"]
    assert third.read_refs(["episodic:latest"])[0]["step_id"] == "node-2:1"


def test_rejected_append_does_not_advance_the_witness(tmp_path):
    path = tmp_path / "events.jsonl"
    held = _relay(path)
    store = WitnessedMemoryStore(path, anchor=held)
    with pytest.raises(ValueError):
        store.append(node_id="node-2", round=1, objective="seed", output=_base(step_id="node-7:3"))
    assert store.witness.current() == held == store.anchor


@pytest.mark.parametrize("case_id", sorted(TAMPERS))
def test_every_earlier_log_tamper_stays_refused(tmp_path, case_id):
    path = tmp_path / "events.jsonl"
    held = _relay(path)
    TAMPERS[case_id](path)
    with pytest.raises(ValueError):
        WitnessedMemoryStore(path, anchor=held).events()


def test_resealed_extension_is_still_not_served(tmp_path):
    path = tmp_path / "events.jsonl"
    held = _relay(path)
    tamper_resealed_extension(path)
    assert _ids(WitnessedMemoryStore(path, anchor=held).events()) == HONEST


def test_witness_rollback_is_a_recorded_miss(tmp_path):
    path = tmp_path / "events.jsonl"
    _relay(path)
    tamper_witness_rollback(path)
    stale = _stale_anchor(path)
    assert AnchorWitness(witness_path_for(path)).current() == stale
    assert _ids(WitnessedMemoryStore(path, anchor=stale).events()) == HONEST[:2]


def test_eight_targets_keep_the_seven_target_columns_unchanged():
    seven = json.loads(SEVEN_TARGET.read_text(encoding="utf-8"))
    eight = build_eight_target_catalog()
    old_rows = {row["case_id"]: row for row in seven["results"]}
    for row in eight["results"]:
        if row["case_id"] in old_rows:
            for target in seven["targets"]:
                assert row[target] == old_rows[row["case_id"]][target]
    assert set(old_rows) | set(WITNESSED_CASES) == {row["case_id"] for row in eight["results"]}
    assert eight["targets"] == seven["targets"] + ["witnessed"]
    assert seven == build_seven_target_catalog()


def test_witnessed_flips_exactly_the_stale_rollback_and_regresses_nothing():
    catalog = build_eight_target_catalog()
    summary = catalog["summary"]
    assert summary["cases"] == 28
    assert summary["witnessed_flipped_from_bounded"] == ["anchor_stale_rollback"]
    assert summary["witnessed_regressed_from_bounded"] == []
    assert summary["witnessed_still_missed"] == ["objective_reasoning_smuggle", "witness_rollback"]
    rows = {row["case_id"]: row for row in catalog["results"]}
    assert rows["anchor_stale_rollback"]["bounded"]["classification"] == "missed"
    assert rows["anchor_stale_rollback"]["witnessed"]["evidence"]["reason"] == "anchor_stale"
    for target in EARLIER + ["witnessed"]:
        assert rows["witness_rollback"][target]["classification"] == "missed", target
    assert rows["witness_rollback"]["witnessed"]["evidence"]["served_step_ids"] == HONEST[:2]


def test_eight_target_catalog_is_deterministic():
    assert build_eight_target_catalog() == build_eight_target_catalog()


def test_committed_eight_target_results_match_a_fresh_run():
    committed = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert committed == build_eight_target_catalog()
