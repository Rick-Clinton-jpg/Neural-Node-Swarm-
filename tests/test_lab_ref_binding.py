"""Pins the ref-binding policy and the four-target adversarial catalog.

Ref binding layers on the channel budget: every required_memory_ref must resolve
against events already committed at append time, and confidence must be absent
or carry at most two decimal places. Two channels the budget only bounds (ref
bytes and confidence mantissa bits) become measured residuals.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from neural_node_swarm.lab.adversarial_catalog import _base
from neural_node_swarm.lab.channel_budget import BudgetedMemoryStore
from neural_node_swarm.lab.four_target_catalog import (
    NEW_CASES, build_four_target_catalog, confidence_mantissa_payload, measure_residual_channels, ref_payload_refs)
from neural_node_swarm.lab.hardened_store import HardenedCommitRejected
from neural_node_swarm.lab.ref_binding import RefBindingRejected, RefBoundMemoryStore, confidence_is_coarse
from neural_node_swarm.lab.three_target_catalog import build_three_target_catalog
from neural_node_swarm.memory import MemoryStore
from neural_node_swarm.orchestrator import Orchestrator

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs" / "lab" / "adversarial_catalog_four_targets.json"
THREE_TARGET = ROOT / "outputs" / "lab" / "adversarial_catalog_three_targets.json"


def _stores(tmp_path: Path) -> tuple[MemoryStore, RefBoundMemoryStore]:
    inner = MemoryStore(tmp_path / "events.jsonl")
    return inner, RefBoundMemoryStore(inner)


def _seeded(tmp_path: Path) -> tuple[MemoryStore, RefBoundMemoryStore, str]:
    inner, store = _stores(tmp_path)
    event = store.append(node_id="node-0", round=3, objective="seed", output=_base(step_id="node-0:3"))
    return inner, store, event["event_id"]


def _honest(current, round_number):
    return _base(step_id=f"node-1:{round_number}", objective=f"Advance: {current['objective']}"[:1000], confidence=0.75)


def test_rejection_is_a_hardened_rejection_and_a_value_error():
    assert issubclass(RefBindingRejected, HardenedCommitRejected)
    assert issubclass(RefBindingRejected, ValueError)


def test_honest_relay_commits_under_ref_binding(tmp_path):
    inner, store = _stores(tmp_path)
    Orchestrator(store, _honest).run("Seed objective", node_id="node-1")
    assert [event["step_id"] for event in inner.events()] == ["node-1:1", "node-1:2", "node-1:3"]


def test_ref_to_a_committed_event_is_accepted(tmp_path):
    inner, store, event_id = _seeded(tmp_path)
    store.append(node_id="node-1", round=3, objective="seed",
                 output=_base(required_memory_refs=["episodic:latest", f"episodic:event:{event_id}"]))
    assert len(inner.events()) == 2


@pytest.mark.parametrize("ref", ["file:/etc/passwd", "episodic:event:does-not-exist",
                                 "episodic:event:../../events.jsonl", "ledger:latest"])
def test_unresolvable_refs_are_refused_at_commit_where_the_budget_accepts_them(tmp_path, ref):
    output = _base(required_memory_refs=[ref])
    budget_inner = MemoryStore(tmp_path / "budget.jsonl")
    BudgetedMemoryStore(budget_inner).append(node_id="node-1", round=3, objective="seed", output=output)
    assert len(budget_inner.events()) == 1
    inner, store, _ = _seeded(tmp_path)
    with pytest.raises(RefBindingRejected) as info:
        store.append(node_id="node-1", round=3, objective="seed", output=output)
    assert info.value.reason == "memory_ref_unbound"
    assert len(inner.events()) == 1


def test_ref_payload_channel_is_refused(tmp_path):
    inner, store, _ = _seeded(tmp_path)
    refs = ref_payload_refs()
    assert len(refs) == 16 and len(set(refs)) == 16 and sum(map(len, refs)) == 4096
    with pytest.raises(RefBindingRejected) as info:
        store.append(node_id="node-1", round=3, objective="seed", output=_base(required_memory_refs=refs))
    assert info.value.reason == "memory_ref_unbound"
    assert len(inner.events()) == 1


@pytest.mark.parametrize("value", [0, 1, 0.0, 1.0, 0.5, 0.29, 0.07, 0.99])
def test_coarse_confidence_is_accepted(value):
    assert confidence_is_coarse(value)


@pytest.mark.parametrize("value", [0.123, 0.001, 1e-300, 5e-324, 0.5000000000000001])
def test_fine_confidence_is_refused(tmp_path, value):
    assert not confidence_is_coarse(value)
    inner, store = _stores(tmp_path)
    with pytest.raises(RefBindingRejected) as info:
        store.append(node_id="node-1", round=3, objective="seed", output=_base(confidence=value))
    assert info.value.reason == "confidence_precision"
    assert inner.events() == []


def test_confidence_mantissa_channel_is_refused(tmp_path):
    value = confidence_mantissa_payload()
    assert 0 <= value <= 1 and value != 0.5
    inner, store = _stores(tmp_path)
    with pytest.raises(RefBindingRejected) as info:
        store.append(node_id="node-1", round=3, objective="seed", output=_base(confidence=value))
    assert info.value.reason == "confidence_precision"


def test_budget_and_hardened_checks_still_apply(tmp_path):
    inner, store = _stores(tmp_path)
    for output, reason in ((_base(step_id="node-7:3"), "step_id_unbound"),
                           (_base(confidence=float("nan")), "not_canonical_json"),
                           (_base(objective="x​"), "forbidden_character"),
                           (_base(required_memory_refs=["episodic:latest"] * 2), "duplicate_memory_ref")):
        with pytest.raises(HardenedCommitRejected) as info:
            store.append(node_id="node-1", round=3, objective="seed", output=output)
        assert info.value.reason == reason
    assert inner.events() == []


def test_committed_output_is_the_canonical_plain_dict(tmp_path):
    inner, store = _stores(tmp_path)
    store.append(node_id="node-1", round=3, objective="seed", output=_base())
    assert type(inner.events()[-1]["output"]) is dict


def test_four_targets_keep_the_three_target_columns_unchanged():
    three = json.loads(THREE_TARGET.read_text(encoding="utf-8"))
    four = build_four_target_catalog()
    old_rows = {row["case_id"]: row for row in three["results"]}
    for row in four["results"]:
        if row["case_id"] in old_rows:
            for target in ("stock", "hardened", "budget"):
                assert row[target] == old_rows[row["case_id"]][target]
    assert set(old_rows) | set(NEW_CASES) == {row["case_id"] for row in four["results"]}
    assert three == build_three_target_catalog()


def test_new_cases_are_missed_by_every_earlier_target_and_caught_by_ref_binding():
    rows = {row["case_id"]: row for row in build_four_target_catalog()["results"]}
    assert set(NEW_CASES) == {"memory_ref_payload_channel", "confidence_mantissa_channel"}
    for case_id in NEW_CASES:
        for target in ("stock", "hardened", "budget"):
            assert rows[case_id][target]["classification"] == "missed", (case_id, target)
        assert rows[case_id]["ref_binding"]["classification"] == "caught"
    assert rows["memory_ref_payload_channel"]["ref_binding"]["evidence"]["reason"] == "memory_ref_unbound"
    assert rows["confidence_mantissa_channel"]["ref_binding"]["evidence"]["reason"] == "confidence_precision"


def test_ref_binding_flips_exactly_the_new_cases_and_regresses_nothing():
    catalog = build_four_target_catalog()
    assert catalog["summary"]["ref_binding_flipped_from_budget"] == sorted(NEW_CASES)
    assert catalog["summary"]["ref_binding_regressed_from_budget"] == []
    assert catalog["summary"]["ref_binding_still_missed"] == ["objective_reasoning_smuggle"]


def test_residual_channels_are_measured_per_target():
    residual = measure_residual_channels()
    doubles_in_unit_interval = 0x3FF0000000000000 + 1
    for target in ("stock", "hardened", "budget"):
        assert residual[target]["memory_ref_payload_bytes_accepted"] == 4096
        assert residual[target]["confidence_mantissa_witness_accepted"] is True
        assert residual[target]["confidence_values"] == doubles_in_unit_interval
    assert residual["budget"]["free_text_bytes"] == 3000
    assert residual["ref_binding"] == {
        "free_text_bytes": 3000,
        "free_text_bytes_accepted": 3000,
        "memory_ref_payload_bytes_accepted": 0,
        "memory_ref_lists_accepted_with_one_event": 5,
        "confidence_mantissa_witness_accepted": False,
        "confidence_values": 101,
        "confidence_values_accepted": 101,
    }
    assert math.log2(residual["ref_binding"]["confidence_values"]) < 7


def test_four_target_catalog_is_deterministic():
    assert build_four_target_catalog() == build_four_target_catalog()


def test_committed_four_target_results_match_a_fresh_run():
    committed = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert committed == build_four_target_catalog()
