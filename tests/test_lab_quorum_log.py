"""Pins the quorum log and the nine-target adversarial catalog.

A witnessed store keeps one witness file, so rolling that file back with the
log (witness_rollback) rewinds freshness. The quorum store keeps N witness
replicas in separate directories, advances every replica on each append, and
takes the highest anchor any replica holds as current, after checking the
replicas agree with each other and with the verified chain. Rolling back all
but one replica (quorum_partial_rollback) is caught; rolling back every replica
(quorum_full_rollback) is a recorded miss.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from neural_node_swarm.lab.adversarial_catalog import _base
from neural_node_swarm.lab.anchored_log import Anchor, AnchorRefused
from neural_node_swarm.lab.eight_target_catalog import build_eight_target_catalog, tamper_witness_rollback
from neural_node_swarm.lab.five_target_catalog import TAMPERS, honest_relay
from neural_node_swarm.lab.nine_target_catalog import (
    QUORUM_CASES, build_nine_target_catalog, tamper_quorum_full_rollback, tamper_quorum_partial_rollback)
from neural_node_swarm.lab.quorum_log import QuorumMemoryStore, QuorumWitness, replica_paths_for
from neural_node_swarm.lab.sealed_log import GENESIS
from neural_node_swarm.lab.seven_target_catalog import tamper_resealed_extension
from neural_node_swarm.lab.witnessed_log import AnchorWitness, WitnessedMemoryStore, witness_path_for

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs" / "lab" / "adversarial_catalog_nine_targets.json"
EIGHT_TARGET = ROOT / "outputs" / "lab" / "adversarial_catalog_eight_targets.json"
EARLIER = ["stock", "hardened", "budget", "ref_binding", "sealed", "anchored", "bounded", "witnessed"]
HONEST = ["node-1:1", "node-1:2", "node-1:3"]


def _relay(path: Path) -> Anchor:
    store = QuorumMemoryStore(path)
    honest_relay(store)
    return store.anchor


def _ids(events):
    return [event["step_id"] for event in events]


def _stale_anchor(path: Path) -> Anchor:
    lines = path.read_text(encoding="utf-8").splitlines()
    return Anchor(len(lines), json.loads(lines[-1])["seal"])


def test_quorum_store_is_a_witnessed_store_with_replicas_in_separate_directories(tmp_path):
    assert issubclass(QuorumMemoryStore, WitnessedMemoryStore)
    path = tmp_path / "events.jsonl"
    held = _relay(path)
    replicas = replica_paths_for(path)
    assert len(replicas) == 3 and replicas[0] == witness_path_for(path)
    assert len({replica.parent for replica in replicas}) == 3
    assert path not in replicas
    assert all(AnchorWitness(replica).current() == held for replica in replicas)


def test_replicas_must_live_in_separate_directories_and_not_be_the_log(tmp_path):
    path = tmp_path / "events.jsonl"
    for paths in ([], [tmp_path / "a.json", tmp_path / "b.json"], [path]):
        with pytest.raises(ValueError):
            QuorumMemoryStore(path, witness_paths=paths)


def test_quorum_witness_takes_the_highest_replica_and_refuses_an_equal_count_fork(tmp_path):
    paths = [tmp_path / f"r{i}" / "w.json" for i in range(3)]
    quorum = QuorumWitness(paths)
    assert quorum.current() == Anchor(0, GENESIS)
    quorum.record(Anchor(2, "a" * 64))
    AnchorWitness(paths[0]).path.write_text(json.dumps({"count": 1, "head": "b" * 64}), encoding="utf-8")
    assert quorum.current() == Anchor(2, "a" * 64)
    quorum.record(Anchor(3, "c" * 64))
    assert all(AnchorWitness(p).current() == Anchor(3, "c" * 64) for p in paths)
    for anchor, reason in ((Anchor(2, "c" * 64), "witness_regress"), (Anchor(3, "d" * 64), "witness_fork")):
        with pytest.raises(AnchorRefused) as info:
            quorum.record(anchor)
        assert info.value.reason == reason
    paths[1].write_text(json.dumps({"count": 3, "head": "e" * 64}), encoding="utf-8")
    with pytest.raises(AnchorRefused) as info:
        quorum.current()
    assert info.value.reason == "witness_fork"


def test_a_replica_off_the_verified_chain_is_refused_as_a_fork(tmp_path):
    path = tmp_path / "events.jsonl"
    held = _relay(path)
    replica_paths_for(path)[1].write_text(json.dumps({"count": 2, "head": "f" * 64}), encoding="utf-8")
    with pytest.raises(AnchorRefused) as info:
        QuorumMemoryStore(path, anchor=held)
    assert info.value.reason == "witness_fork"


def test_witness_rollback_is_missed_by_witnessed_and_caught_by_quorum(tmp_path):
    for store_class in (WitnessedMemoryStore, QuorumMemoryStore):
        path = tmp_path / store_class.__name__ / "events.jsonl"
        store = store_class(path)
        honest_relay(store)
        tamper_witness_rollback(path)
        stale = _stale_anchor(path)
        if store_class is WitnessedMemoryStore:
            assert _ids(store_class(path, anchor=stale).events()) == HONEST[:2]
        else:
            with pytest.raises(AnchorRefused) as info:
                store_class(path, anchor=stale)
            assert info.value.reason == "anchor_stale"
            with pytest.raises(AnchorRefused):
                store_class(path)


def test_partial_rollback_leaving_one_replica_is_caught(tmp_path):
    path = tmp_path / "events.jsonl"
    _relay(path)
    tamper_quorum_partial_rollback(path)
    stale = _stale_anchor(path)
    replicas = [AnchorWitness(p).current().count for p in replica_paths_for(path)]
    assert replicas == [2, 2, 3]
    with pytest.raises(AnchorRefused) as info:
        QuorumMemoryStore(path, anchor=stale)
    assert info.value.reason == "anchor_stale"


def test_full_rollback_of_every_replica_is_a_recorded_miss(tmp_path):
    path = tmp_path / "events.jsonl"
    _relay(path)
    tamper_quorum_full_rollback(path)
    stale = _stale_anchor(path)
    assert all(AnchorWitness(p).current() == stale for p in replica_paths_for(path))
    assert _ids(QuorumMemoryStore(path, anchor=stale).events()) == HONEST[:2]


def test_a_rolled_back_replica_is_healed_by_the_next_honest_append(tmp_path):
    path = tmp_path / "events.jsonl"
    held = _relay(path)
    replica_paths_for(path)[0].write_text(json.dumps({"count": 1, "head": "a" * 64}), encoding="utf-8")
    with pytest.raises(AnchorRefused) as info:
        QuorumMemoryStore(path, anchor=held)  # replica 0 is off the chain at count 1
    assert info.value.reason == "witness_fork"
    replica_paths_for(path)[0].unlink()
    store = QuorumMemoryStore(path, anchor=held)
    nxt = store.append_anchored(node_id="node-2", round=1, objective="seed", output=_base(step_id="node-2:1"))
    assert all(AnchorWitness(p).current() == nxt for p in replica_paths_for(path))


def test_honest_relay_passing_its_anchor_forward_commits_and_reads_back(tmp_path):
    path = tmp_path / "events.jsonl"
    store = QuorumMemoryStore(path)
    assert store.anchor == Anchor(0, GENESIS)
    honest_relay(store)
    held = store.anchor
    assert held.count == 3 and store.witness.current() == held
    successor = QuorumMemoryStore(path, anchor=held)
    assert _ids(successor.events()) == HONEST
    nxt = successor.append_anchored(node_id="node-2", round=1, objective="seed", output=_base(step_id="node-2:1"))
    assert nxt.count == 4
    third = QuorumMemoryStore(path, anchor=nxt)
    assert _ids(third.events()) == HONEST + ["node-2:1"]
    assert third.read_refs(["episodic:latest"])[0]["step_id"] == "node-2:1"


def test_rejected_append_does_not_advance_any_replica(tmp_path):
    path = tmp_path / "events.jsonl"
    held = _relay(path)
    store = QuorumMemoryStore(path, anchor=held)
    with pytest.raises(ValueError):
        store.append(node_id="node-2", round=1, objective="seed", output=_base(step_id="node-7:3"))
    assert all(AnchorWitness(p).current() == held for p in replica_paths_for(path))


@pytest.mark.parametrize("case_id", sorted(TAMPERS))
def test_every_earlier_log_tamper_stays_refused(tmp_path, case_id):
    path = tmp_path / "events.jsonl"
    held = _relay(path)
    TAMPERS[case_id](path)
    with pytest.raises(ValueError):
        QuorumMemoryStore(path, anchor=held).events()


def test_resealed_extension_is_still_not_served(tmp_path):
    path = tmp_path / "events.jsonl"
    held = _relay(path)
    tamper_resealed_extension(path)
    assert _ids(QuorumMemoryStore(path, anchor=held).events()) == HONEST


def test_nine_targets_keep_the_eight_target_columns_unchanged():
    eight = json.loads(EIGHT_TARGET.read_text(encoding="utf-8"))
    nine = build_nine_target_catalog()
    old_rows = {row["case_id"]: row for row in eight["results"]}
    for row in nine["results"]:
        if row["case_id"] in old_rows:
            for target in eight["targets"]:
                assert row[target] == old_rows[row["case_id"]][target]
    assert set(old_rows) | set(QUORUM_CASES) == {row["case_id"] for row in nine["results"]}
    assert nine["targets"] == eight["targets"] + ["quorum"]
    assert eight == build_eight_target_catalog()


def test_quorum_flips_exactly_the_single_witness_rollbacks_and_regresses_nothing():
    catalog = build_nine_target_catalog()
    summary = catalog["summary"]
    assert summary["cases"] == 30
    assert summary["quorum_flipped_from_witnessed"] == ["quorum_partial_rollback", "witness_rollback"]
    assert summary["quorum_regressed_from_witnessed"] == []
    assert summary["quorum_still_missed"] == ["objective_reasoning_smuggle", "quorum_full_rollback"]
    rows = {row["case_id"]: row for row in catalog["results"]}
    assert rows["witness_rollback"]["witnessed"]["classification"] == "missed"
    assert rows["witness_rollback"]["quorum"]["evidence"]["reason"] == "anchor_stale"
    assert rows["quorum_partial_rollback"]["quorum"]["evidence"]["reason"] == "anchor_stale"
    for case_id in QUORUM_CASES:
        for target in EARLIER:
            assert rows[case_id][target]["classification"] == "missed", (case_id, target)
    assert rows["quorum_full_rollback"]["quorum"]["evidence"]["served_step_ids"] == HONEST[:2]


def test_nine_target_catalog_is_deterministic():
    assert build_nine_target_catalog() == build_nine_target_catalog()


def test_committed_nine_target_results_match_a_fresh_run():
    committed = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert committed == build_nine_target_catalog()
