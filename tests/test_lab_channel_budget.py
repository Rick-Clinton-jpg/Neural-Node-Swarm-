"""Pins the channel-budget policy and the three-target adversarial catalog.

The budget policy layers on the hardened commit path: it canonicalizes once,
refuses Unicode control/format characters in every string, refuses duplicate or
excess memory refs, and caps the item count and UTF-8 bytes of every free-text
field. The residual side channel each target leaves open is a measured number.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from neural_node_swarm.lab import channel_budget as budget
from neural_node_swarm.lab.adversarial_catalog import _base
from neural_node_swarm.lab.channel_budget import BudgetedMemoryStore, BudgetRejected
from neural_node_swarm.lab.hardened_store import HardenedCommitRejected
from neural_node_swarm.lab.three_target_catalog import build_three_target_catalog, measure_channel_capacity
from neural_node_swarm.memory import MemoryStore
from neural_node_swarm.orchestrator import Orchestrator

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs" / "lab" / "adversarial_catalog_three_targets.json"
TWO_TARGET = ROOT / "outputs" / "lab" / "adversarial_catalog_two_targets.json"

BUDGET_FLIPS = {"objective_zero_width_payload", "memory_ref_amplification",
                "success_criteria_unbounded_channel", "constraints_reasoning_smuggle"}


def _store(tmp_path: Path) -> tuple[MemoryStore, BudgetedMemoryStore]:
    inner = MemoryStore(tmp_path / "events.jsonl")
    return inner, BudgetedMemoryStore(inner)


def _reject(tmp_path: Path, output) -> str:
    inner, store = _store(tmp_path)
    with pytest.raises(HardenedCommitRejected) as info:
        store.append(node_id="node-1", round=3, objective="seed", output=output)
    assert inner.events() == []
    return info.value.reason


def _honest(current, round_number):
    return _base(step_id=f"node-1:{round_number}", objective=f"Advance: {current['objective']}"[:1000])


def test_rejection_is_a_hardened_rejection_and_a_value_error():
    assert issubclass(BudgetRejected, HardenedCommitRejected)
    assert issubclass(BudgetRejected, ValueError)


def test_honest_relay_commits_under_the_budget(tmp_path):
    inner, store = _store(tmp_path)
    Orchestrator(store, _honest).run("Seed objective", node_id="node-1")
    assert [event["step_id"] for event in inner.events()] == ["node-1:1", "node-1:2", "node-1:3"]


@pytest.mark.parametrize("char", ["​", "‌", "⁠", "﻿", "‮", "\n", "\x00", "\x7f", ""])
@pytest.mark.parametrize("field", ["objective", "success_criteria", "constraints"])
def test_control_and_format_characters_are_refused_in_every_string(tmp_path, field, char):
    value = "Add one lab test." + char
    output = _base(**{field: value if field == "objective" else [value]})
    assert _reject(tmp_path, output) == "forbidden_character"


def test_lone_surrogate_is_refused_not_crashed(tmp_path):
    assert _reject(tmp_path, _base(objective="Add one lab test.\ud800")) in {"forbidden_character", "not_canonical_json"}


def test_duplicate_memory_refs_are_refused(tmp_path):
    assert _reject(tmp_path, _base(required_memory_refs=["episodic:latest", "episodic:latest"])) == "duplicate_memory_ref"


def test_memory_ref_count_is_capped(tmp_path):
    refs = [f"episodic:event:e{i}" for i in range(budget.MAX_MEMORY_REFS + 1)]
    assert _reject(tmp_path, _base(required_memory_refs=refs)) == "memory_ref_count"


def test_read_refs_refuses_amplification(tmp_path):
    _inner, store = _store(tmp_path)
    store.append(node_id="node-0", round=3, objective="seed", output=_base(step_id="node-0:3"))
    with pytest.raises(BudgetRejected):
        store.read_refs(["episodic:latest"] * 500)
    assert len(store.read_refs(["episodic:latest"])) == 1


@pytest.mark.parametrize("field", ["success_criteria", "constraints"])
def test_item_count_is_capped(tmp_path, field):
    output = _base(**{field: [f"item {i}" for i in range(budget.MAX_ITEMS + 1)]})
    assert _reject(tmp_path, output) == f"{field}_count"


@pytest.mark.parametrize("field", ["success_criteria", "constraints"])
def test_total_bytes_are_capped(tmp_path, field):
    output = _base(**{field: ["a" * 500, "b" * 500, "c"]})
    assert _reject(tmp_path, output) == f"{field}_bytes"


def test_objective_bytes_are_capped_even_within_the_char_cap(tmp_path):
    output = _base(objective="é" * 501)  # 501 chars, 1002 UTF-8 bytes
    assert _reject(tmp_path, output) == "objective_bytes"


def test_hardened_checks_still_apply(tmp_path):
    assert _reject(tmp_path, _base(step_id="node-7:3")) == "step_id_unbound"
    assert _reject(tmp_path, _base(confidence=float("nan"))) == "not_canonical_json"


def test_committed_output_is_the_canonical_plain_dict(tmp_path):
    inner, store = _store(tmp_path)
    store.append(node_id="node-1", round=3, objective="seed", output=_base(step_id="node-1:3"))
    assert type(inner.events()[-1]["output"]) is dict


def test_three_targets_keep_the_two_target_columns_unchanged():
    two = json.loads(TWO_TARGET.read_text(encoding="utf-8"))
    three = build_three_target_catalog()
    for old, new in zip(two["results"], three["results"]):
        assert old["case_id"] == new["case_id"]
        assert old["stock"] == new["stock"]
        assert old["hardened"] == new["hardened"]


def test_budget_flips_exactly_the_expected_cases_and_regresses_nothing():
    catalog = build_three_target_catalog()
    rows = catalog["results"]
    flipped = {r["case_id"] for r in rows
               if r["hardened"]["classification"] == "missed" and r["budget"]["classification"] == "caught"}
    regressed = [r["case_id"] for r in rows
                 if r["hardened"]["classification"] == "caught" and r["budget"]["classification"] == "missed"]
    assert flipped == BUDGET_FLIPS
    assert regressed == []
    assert catalog["summary"]["budget_flipped_from_hardened"] == sorted(BUDGET_FLIPS)


def test_objective_reasoning_smuggle_stays_a_recorded_miss():
    row = next(r for r in build_three_target_catalog()["results"] if r["case_id"] == "objective_reasoning_smuggle")
    assert row["budget"]["classification"] == "missed"


def test_residual_channel_capacity_is_measured_per_target():
    capacity = measure_channel_capacity()
    assert capacity["stock"]["free_text_bytes"] is None and capacity["stock"]["free_text_bounded"] is False
    assert capacity["hardened"]["free_text_bytes"] is None and capacity["hardened"]["free_text_bounded"] is False
    assert capacity["stock"]["unbounded_witness_bytes_accepted"] >= 100_000
    assert capacity["hardened"]["unbounded_witness_bytes_accepted"] >= 100_000
    pinned = budget.OBJECTIVE_MAX_BYTES + budget.LIST_MAX_BYTES * 2
    assert capacity["budget"] == {
        "free_text_bounded": True,
        "free_text_bytes": pinned,
        "free_text_bytes_accepted": pinned,
        "one_more_byte_rejected": True,
        "memory_ref_bytes_accepted": budget.MAX_MEMORY_REFS * 256,
    }
    assert pinned == 3000


def test_three_target_catalog_is_deterministic():
    assert build_three_target_catalog() == build_three_target_catalog()


def test_committed_three_target_results_match_a_fresh_run():
    committed = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert committed == build_three_target_catalog()
