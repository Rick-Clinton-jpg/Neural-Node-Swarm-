"""Pins the sealed event log and the five-target adversarial catalog.

Every earlier target trusts events.jsonl on disk. The sealed log chains each
committed event to its predecessor with SHA-256 and re-verifies the chain on
every read, so a line rewritten, inserted or reordered after commit is refused
before it reaches read_refs or a successor. A chain alone cannot see a dropped
tail or an attacker who recomputes every seal: both are pinned as recorded misses.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from neural_node_swarm.lab.adversarial_catalog import _base
from neural_node_swarm.lab.five_target_catalog import (
    LOG_CASES, TAMPERS, build_five_target_catalog, honest_relay)
from neural_node_swarm.lab.four_target_catalog import build_four_target_catalog
from neural_node_swarm.lab.hardened_store import HardenedCommitRejected
from neural_node_swarm.lab.ref_binding import RefBindingRejected, RefBoundMemoryStore
from neural_node_swarm.lab.sealed_log import GENESIS, SealBroken, SealedMemoryStore, seal_of
from neural_node_swarm.memory import MemoryStore

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs" / "lab" / "adversarial_catalog_five_targets.json"
FOUR_TARGET = ROOT / "outputs" / "lab" / "adversarial_catalog_four_targets.json"
DETECTED = ["log_event_insert", "log_event_reorder", "log_event_rewrite"]
RECORDED_MISSES = ["log_reseal_rewrite", "log_tail_truncation"]


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _ref_bound(path: Path) -> RefBoundMemoryStore:
    return RefBoundMemoryStore(MemoryStore(path))


def test_seal_broken_is_a_hardened_rejection_and_a_value_error():
    assert issubclass(SealBroken, HardenedCommitRejected)
    assert issubclass(SealBroken, ValueError)


def test_honest_relay_commits_seals_and_reads_back(tmp_path):
    path = tmp_path / "events.jsonl"
    store = SealedMemoryStore(path)
    honest_relay(store)
    assert [event["step_id"] for event in store.events()] == ["node-1:1", "node-1:2", "node-1:3"]
    assert store.read_refs(["episodic:latest"])[0]["step_id"] == "node-1:3"
    previous = GENESIS
    for line in _lines(path):
        seal = line.pop("seal")
        assert seal == seal_of(previous, line)
        previous = seal
    assert all("seal" not in event for event in store.events())


def test_sealed_store_keeps_the_ref_binding_policy(tmp_path):
    store = SealedMemoryStore(tmp_path / "events.jsonl")
    for output, reason in ((_base(step_id="node-7:3"), "step_id_unbound"),
                           (_base(confidence=0.123), "confidence_precision"),
                           (_base(required_memory_refs=["episodic:event:missing"]), "memory_ref_unbound")):
        with pytest.raises(HardenedCommitRejected) as info:
            store.append(node_id="node-1", round=3, objective="seed", output=output)
        assert info.value.reason == reason
    assert store.events() == []


@pytest.mark.parametrize("case_id", DETECTED)
def test_tamper_is_missed_by_ref_binding_and_caught_by_the_sealed_log(tmp_path, case_id):
    plain, sealed = tmp_path / "plain.jsonl", tmp_path / "sealed.jsonl"
    honest_relay(_ref_bound(plain))
    honest_relay(SealedMemoryStore(sealed))
    TAMPERS[case_id](plain)
    TAMPERS[case_id](sealed)
    assert _ref_bound(plain).read_refs(["episodic:latest"])  # tampered log is served unverified
    store = SealedMemoryStore(sealed)
    with pytest.raises(SealBroken):
        store.events()
    with pytest.raises(SealBroken):
        store.read_refs(["episodic:latest"])


def test_append_onto_a_broken_log_is_refused_and_writes_nothing(tmp_path):
    path = tmp_path / "events.jsonl"
    honest_relay(SealedMemoryStore(path))
    TAMPERS["log_event_rewrite"](path)
    before = path.read_bytes()
    with pytest.raises(SealBroken):
        SealedMemoryStore(path).append(node_id="node-2", round=1, objective="seed",
                                       output=_base(step_id="node-2:1"))
    assert path.read_bytes() == before


@pytest.mark.parametrize("case_id", RECORDED_MISSES)
def test_chain_limits_are_recorded_misses(tmp_path, case_id):
    path = tmp_path / "events.jsonl"
    honest_relay(SealedMemoryStore(path))
    TAMPERS[case_id](path)
    events = SealedMemoryStore(path).events()  # no exception: the chain cannot see this
    if case_id == "log_tail_truncation":
        assert [event["step_id"] for event in events] == ["node-1:1", "node-1:2"]
    else:
        assert events[0]["output"]["objective"].startswith("FORGED")


def test_five_targets_keep_the_four_target_columns_unchanged():
    four = json.loads(FOUR_TARGET.read_text(encoding="utf-8"))
    five = build_five_target_catalog()
    old_rows = {row["case_id"]: row for row in four["results"]}
    for row in five["results"]:
        if row["case_id"] in old_rows:
            for target in four["targets"]:
                assert row[target] == old_rows[row["case_id"]][target]
    assert set(old_rows) | set(LOG_CASES) == {row["case_id"] for row in five["results"]}
    assert four == build_four_target_catalog()


def test_log_cases_are_missed_by_every_earlier_target():
    rows = {row["case_id"]: row for row in build_five_target_catalog()["results"]}
    assert sorted(LOG_CASES) == sorted(DETECTED + RECORDED_MISSES)
    for case_id in LOG_CASES:
        for target in ("stock", "hardened", "budget", "ref_binding"):
            assert rows[case_id][target]["classification"] == "missed", (case_id, target)
    for case_id in DETECTED:
        assert rows[case_id]["sealed"]["classification"] == "caught"
        assert rows[case_id]["sealed"]["evidence"]["reason"].startswith("log_seal_")


def test_sealed_flips_exactly_the_tamper_cases_and_regresses_nothing():
    summary = build_five_target_catalog()["summary"]
    assert summary["sealed_flipped_from_ref_binding"] == DETECTED
    assert summary["sealed_regressed_from_ref_binding"] == []
    assert summary["sealed_still_missed"] == sorted(["objective_reasoning_smuggle"] + RECORDED_MISSES)
    assert summary["cases"] == 24


def test_five_target_catalog_is_deterministic():
    assert build_five_target_catalog() == build_five_target_catalog()


def test_committed_five_target_results_match_a_fresh_run():
    committed = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert committed == build_five_target_catalog()
