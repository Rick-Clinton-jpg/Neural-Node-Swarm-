"""Pins the bounded log and the seven-target adversarial catalog.

An anchored store accepts any log that extends its anchor, so a forged event
appended past the anchor with a correctly recomputed seal (SHA-256 is unkeyed)
is served as episodic:latest. The bounded store serves events, read_refs and
episodic:latest only from the anchored prefix, refuses to append onto lines the
anchor does not cover, and advances the anchor only with its own appends.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from neural_node_swarm.lab.adversarial_catalog import _base
from neural_node_swarm.lab.anchored_log import Anchor, AnchorRefused, AnchoredMemoryStore
from neural_node_swarm.lab.bounded_log import BoundedMemoryStore
from neural_node_swarm.lab.five_target_catalog import TAMPERS, honest_relay
from neural_node_swarm.lab.sealed_log import GENESIS
from neural_node_swarm.lab.seven_target_catalog import (
    BOUNDED_CASES, build_seven_target_catalog, tamper_resealed_extension)
from neural_node_swarm.lab.six_target_catalog import build_six_target_catalog

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs" / "lab" / "adversarial_catalog_seven_targets.json"
SIX_TARGET = ROOT / "outputs" / "lab" / "adversarial_catalog_six_targets.json"
EARLIER = ["stock", "hardened", "budget", "ref_binding", "sealed", "anchored"]
HONEST = ["node-1:1", "node-1:2", "node-1:3"]


def _relay(path: Path) -> Anchor:
    store = BoundedMemoryStore(path)
    honest_relay(store)
    return store.anchor


def _ids(events):
    return [event["step_id"] for event in events]


def test_bounded_store_is_an_anchored_store():
    assert issubclass(BoundedMemoryStore, AnchoredMemoryStore)


def test_resealed_extension_is_served_by_anchored_and_not_by_bounded(tmp_path):
    path = tmp_path / "events.jsonl"
    held = _relay(path)
    tamper_resealed_extension(path)
    assert len(path.read_text(encoding="utf-8").splitlines()) == 4
    anchored = AnchoredMemoryStore(path, anchor=held)
    assert anchored.read_refs(["episodic:latest"])[0]["step_id"] == "node-9:3"  # the forged line
    bounded = BoundedMemoryStore(path, anchor=held)
    assert _ids(bounded.events()) == HONEST
    assert bounded.read_refs(["episodic:latest"])[0]["step_id"] == "node-1:3"
    with pytest.raises(KeyError):
        bounded.read_refs(["episodic:event:evt-forged-extension"])


def test_append_onto_unanchored_lines_is_refused_and_writes_nothing(tmp_path):
    path = tmp_path / "events.jsonl"
    held = _relay(path)
    tamper_resealed_extension(path)
    before = path.read_bytes()
    store = BoundedMemoryStore(path, anchor=held)
    with pytest.raises(AnchorRefused) as info:
        store.append(node_id="node-2", round=1, objective="seed", output=_base(step_id="node-2:1"))
    assert info.value.reason == "anchor_unanchored_tail"
    assert path.read_bytes() == before and store.anchor == held


def test_honest_relay_passing_its_anchor_forward_commits_and_reads_back(tmp_path):
    path = tmp_path / "events.jsonl"
    store = BoundedMemoryStore(path)
    assert store.anchor == Anchor(0, GENESIS)
    honest_relay(store)
    held = store.anchor
    assert held.count == 3
    successor = BoundedMemoryStore(path, anchor=held)
    assert _ids(successor.events()) == HONEST
    nxt = successor.append_anchored(node_id="node-2", round=1, objective="seed", output=_base(step_id="node-2:1"))
    assert nxt.count == 4 and nxt != held
    assert successor.read_refs(["episodic:latest"])[0]["step_id"] == "node-2:1"
    third = BoundedMemoryStore(path, anchor=nxt)
    assert _ids(third.events()) == HONEST + ["node-2:1"]
    assert third.read_refs(["episodic:latest"])[0]["step_id"] == "node-2:1"


def test_an_older_anchor_sees_only_its_prefix_and_cannot_extend(tmp_path):
    path = tmp_path / "events.jsonl"
    store = BoundedMemoryStore(path)
    store.append(node_id="node-0", round=3, objective="seed", output=_base(step_id="node-0:3"))
    older = store.anchor
    honest_relay(store)
    stale_view = BoundedMemoryStore(path, anchor=older)
    assert _ids(stale_view.events()) == ["node-0:3"]
    with pytest.raises(AnchorRefused):
        stale_view.append(node_id="node-2", round=1, objective="seed", output=_base(step_id="node-2:1"))


@pytest.mark.parametrize("case_id", sorted(TAMPERS))
def test_every_earlier_log_tamper_stays_refused(tmp_path, case_id):
    path = tmp_path / "events.jsonl"
    held = _relay(path)
    TAMPERS[case_id](path)
    with pytest.raises(ValueError):
        BoundedMemoryStore(path, anchor=held).events()


def test_stale_rollback_stays_a_recorded_miss(tmp_path):
    path = tmp_path / "events.jsonl"
    _relay(path)
    stale = Anchor(2, json.loads(path.read_text(encoding="utf-8").splitlines()[1])["seal"])
    TAMPERS["log_tail_truncation"](path)
    assert _ids(BoundedMemoryStore(path, anchor=stale).events()) == HONEST[:2]


def test_bounded_store_keeps_the_ref_binding_policy(tmp_path):
    store = BoundedMemoryStore(tmp_path / "events.jsonl")
    for output, reason in ((_base(step_id="node-7:3"), "step_id_unbound"),
                           (_base(confidence=0.123), "confidence_precision"),
                           (_base(required_memory_refs=["episodic:event:missing"]), "memory_ref_unbound")):
        with pytest.raises(ValueError) as info:
            store.append(node_id="node-1", round=3, objective="seed", output=output)
        assert info.value.reason == reason
    assert store.events() == [] and store.anchor == Anchor(0, GENESIS)


def test_seven_targets_keep_the_six_target_columns_unchanged():
    six = json.loads(SIX_TARGET.read_text(encoding="utf-8"))
    seven = build_seven_target_catalog()
    old_rows = {row["case_id"]: row for row in six["results"]}
    for row in seven["results"]:
        if row["case_id"] in old_rows:
            for target in six["targets"]:
                assert row[target] == old_rows[row["case_id"]][target]
    assert set(old_rows) | set(BOUNDED_CASES) == {row["case_id"] for row in seven["results"]}
    assert seven["targets"] == six["targets"] + ["bounded"]
    assert six == build_six_target_catalog()


def test_bounded_flips_exactly_the_extension_and_regresses_nothing():
    catalog = build_seven_target_catalog()
    summary = catalog["summary"]
    assert summary["cases"] == 27
    assert summary["bounded_flipped_from_anchored"] == ["anchor_resealed_extension"]
    assert summary["bounded_regressed_from_anchored"] == []
    assert summary["bounded_still_missed"] == ["anchor_stale_rollback", "objective_reasoning_smuggle"]
    rows = {row["case_id"]: row for row in catalog["results"]}
    for target in EARLIER:
        assert rows["anchor_resealed_extension"][target]["classification"] == "missed", target
    assert rows["anchor_resealed_extension"]["anchored"]["evidence"]["latest_step_id"] == "node-9:3"
    evidence = rows["anchor_resealed_extension"]["bounded"]["evidence"]
    assert evidence["served_step_ids"] == HONEST and evidence["events_on_disk"] == 4


def test_seven_target_catalog_is_deterministic():
    assert build_seven_target_catalog() == build_seven_target_catalog()


def test_committed_seven_target_results_match_a_fresh_run():
    committed = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert committed == build_seven_target_catalog()
