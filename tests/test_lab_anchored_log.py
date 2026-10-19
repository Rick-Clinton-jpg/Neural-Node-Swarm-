"""Pins the anchored log and the six-target adversarial catalog.

The sealed log re-verifies its chain on every read, but the chain lives inside
events.jsonl, so a dropped tail (a valid prefix) and a fully resealed rewrite
are served. The anchored log hands the caller an anchor (event count, head
seal) after each append. A store opened with that anchor refuses to serve or
extend a log whose verified chain is shorter than the anchor or whose seal at
that count differs. Rolling back to an older anchor the caller still accepts is
outside what an anchor can see and is pinned as a recorded miss.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from neural_node_swarm.lab.adversarial_catalog import _base
from neural_node_swarm.lab.anchored_log import Anchor, AnchorRefused, AnchoredMemoryStore
from neural_node_swarm.lab.five_target_catalog import TAMPERS, build_five_target_catalog, honest_relay
from neural_node_swarm.lab.sealed_log import GENESIS, SealBroken, SealedMemoryStore
from neural_node_swarm.lab.six_target_catalog import ANCHOR_CASES, build_six_target_catalog

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs" / "lab" / "adversarial_catalog_six_targets.json"
FIVE_TARGET = ROOT / "outputs" / "lab" / "adversarial_catalog_five_targets.json"
CHAIN_LIMITS = ["log_reseal_rewrite", "log_tail_truncation"]
EARLIER = ["stock", "hardened", "budget", "ref_binding", "sealed"]


def _relay(path: Path) -> tuple[AnchoredMemoryStore, Anchor]:
    store = AnchoredMemoryStore(path)
    honest_relay(store)
    return store, store.anchor


def test_anchor_refused_is_a_seal_broken_and_a_value_error():
    assert issubclass(AnchorRefused, SealBroken)
    assert issubclass(AnchorRefused, ValueError)


def test_each_append_returns_an_anchor_and_an_honest_relay_reads_back(tmp_path):
    path = tmp_path / "events.jsonl"
    store = AnchoredMemoryStore(path)
    assert store.anchor == Anchor(0, GENESIS)
    first = store.append_anchored(node_id="node-0", round=3, objective="seed",
                                  output=_base(step_id="node-0:3"))
    assert first == Anchor(1, SealedMemoryStore(path).storage.head())
    honest_relay(store)
    held = store.anchor
    assert held.count == 4 and held.head == SealedMemoryStore(path).storage.head()
    successor = AnchoredMemoryStore(path, anchor=held)
    assert [event["step_id"] for event in successor.events()] == ["node-0:3", "node-1:1", "node-1:2", "node-1:3"]
    assert successor.read_refs(["episodic:latest"])[0]["step_id"] == "node-1:3"
    nxt = successor.append_anchored(node_id="node-2", round=1, objective="seed", output=_base(step_id="node-2:1"))
    assert nxt.count == 5 and nxt != held
    assert AnchoredMemoryStore(path, anchor=nxt).events()[-1]["step_id"] == "node-2:1"


def test_an_older_honest_anchor_still_opens_a_longer_log(tmp_path):
    path = tmp_path / "events.jsonl"
    store = AnchoredMemoryStore(path)
    store.append(node_id="node-0", round=3, objective="seed", output=_base(step_id="node-0:3"))
    older = store.anchor
    honest_relay(store)
    assert len(AnchoredMemoryStore(path, anchor=older).events()) == 4


@pytest.mark.parametrize("case_id", CHAIN_LIMITS)
def test_chain_limits_are_missed_by_sealed_and_caught_by_anchored(tmp_path, case_id):
    sealed_path, anchored_path = tmp_path / "sealed.jsonl", tmp_path / "anchored.jsonl"
    honest_relay(SealedMemoryStore(sealed_path))
    _, held = _relay(anchored_path)
    TAMPERS[case_id](sealed_path)
    TAMPERS[case_id](anchored_path)
    assert SealedMemoryStore(sealed_path).events()  # served: the chain cannot see this
    store = AnchoredMemoryStore(anchored_path, anchor=held)
    with pytest.raises(AnchorRefused) as info:
        store.events()
    assert info.value.reason == {"log_tail_truncation": "anchor_rollback",
                                 "log_reseal_rewrite": "anchor_head_mismatch"}[case_id]
    with pytest.raises(AnchorRefused):
        store.read_refs(["episodic:latest"])


def test_append_onto_a_rolled_back_log_is_refused_and_writes_nothing(tmp_path):
    path = tmp_path / "events.jsonl"
    _, held = _relay(path)
    TAMPERS["log_tail_truncation"](path)
    before = path.read_bytes()
    with pytest.raises(AnchorRefused):
        AnchoredMemoryStore(path, anchor=held).append(node_id="node-2", round=1, objective="seed",
                                                      output=_base(step_id="node-2:1"))
    assert path.read_bytes() == before


def test_forged_and_malformed_anchors_are_refused(tmp_path):
    path = tmp_path / "events.jsonl"
    _, held = _relay(path)
    with pytest.raises(AnchorRefused) as info:
        AnchoredMemoryStore(path, anchor=Anchor(held.count, "f" * 64)).events()
    assert info.value.reason == "anchor_head_mismatch"
    with pytest.raises(AnchorRefused) as info:
        AnchoredMemoryStore(path, anchor=Anchor(held.count + 1, held.head)).events()
    assert info.value.reason == "anchor_rollback"
    for bad in (Anchor(-1, GENESIS), Anchor(True, GENESIS), Anchor(1, "xyz"), ("3", held.head)):
        with pytest.raises(AnchorRefused) as info:
            AnchoredMemoryStore(path, anchor=bad)
        assert info.value.reason == "anchor_malformed"


def test_stale_rollback_is_a_recorded_miss(tmp_path):
    path = tmp_path / "events.jsonl"
    store = AnchoredMemoryStore(path)
    honest_relay(store)
    stale = Anchor(2, json.loads(path.read_text(encoding="utf-8").splitlines()[1])["seal"])
    TAMPERS["log_tail_truncation"](path)
    events = AnchoredMemoryStore(path, anchor=stale).events()  # no exception: the anchor is stale, not wrong
    assert [event["step_id"] for event in events] == ["node-1:1", "node-1:2"]


def test_anchored_store_keeps_the_ref_binding_policy(tmp_path):
    store = AnchoredMemoryStore(tmp_path / "events.jsonl")
    for output, reason in ((_base(step_id="node-7:3"), "step_id_unbound"),
                           (_base(confidence=0.123), "confidence_precision"),
                           (_base(required_memory_refs=["episodic:event:missing"]), "memory_ref_unbound")):
        with pytest.raises(ValueError) as info:
            store.append(node_id="node-1", round=3, objective="seed", output=output)
        assert info.value.reason == reason
    assert store.events() == [] and store.anchor == Anchor(0, GENESIS)


def test_six_targets_keep_the_five_target_columns_unchanged():
    five = json.loads(FIVE_TARGET.read_text(encoding="utf-8"))
    six = build_six_target_catalog()
    old_rows = {row["case_id"]: row for row in five["results"]}
    for row in six["results"]:
        if row["case_id"] in old_rows:
            for target in five["targets"]:
                assert row[target] == old_rows[row["case_id"]][target]
    assert set(old_rows) | set(ANCHOR_CASES) == {row["case_id"] for row in six["results"]}
    assert five == build_five_target_catalog()


def test_anchored_flips_exactly_the_chain_limits_and_regresses_nothing():
    catalog = build_six_target_catalog()
    summary = catalog["summary"]
    assert summary["cases"] == 26
    assert summary["anchored_flipped_from_sealed"] == sorted(CHAIN_LIMITS + ["anchor_forged_head"])
    assert summary["anchored_regressed_from_sealed"] == []
    assert summary["anchored_still_missed"] == ["anchor_stale_rollback", "objective_reasoning_smuggle"]
    rows = {row["case_id"]: row for row in catalog["results"]}
    for case_id in ANCHOR_CASES:
        for target in EARLIER:
            assert rows[case_id][target]["classification"] == "missed", (case_id, target)
    assert rows["anchor_forged_head"]["anchored"]["evidence"]["reason"] == "anchor_head_mismatch"


def test_six_target_catalog_is_deterministic():
    assert build_six_target_catalog() == build_six_target_catalog()


def test_committed_six_target_results_match_a_fresh_run():
    committed = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert committed == build_six_target_catalog()
