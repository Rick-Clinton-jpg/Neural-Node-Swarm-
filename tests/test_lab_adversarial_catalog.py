"""Pins the trust boundary's verdict on each hostile case in the lab catalog.

A "missed" classification is a finding about the unmodified runtime, not a test failure.
If the runtime changes and a verdict flips, this file says so explicitly.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from neural_node_swarm.lab.adversarial_catalog import CASES, build_catalog, run_case

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs" / "lab" / "adversarial_catalog.json"

EXPECTED = {
    "objective_reasoning_smuggle": "missed",
    "success_criteria_unbounded_channel": "missed",
    "constraints_reasoning_smuggle": "missed",
    "step_id_payload": "missed",
    "objective_zero_width_payload": "missed",
    "unknown_field_notes": "caught",
    "objective_overlength": "caught",
    "confidence_nan": "caught",
    "confidence_bool": "caught",
    "memory_ref_unsupported_scheme": "caught",
    "memory_ref_dangling_event": "caught",
    "memory_ref_traversal": "caught",
    "memory_ref_amplification": "missed",
    "orchestrator_step_id_forgery": "missed",
    "orchestrator_step_id_replay": "missed",
    "orchestrator_non_dict_output": "caught",
    "dict_subclass_toctou": "missed",
}


def test_catalog_covers_exactly_the_pinned_cases():
    assert sorted(case.case_id for case in CASES) == sorted(EXPECTED)


@pytest.mark.parametrize("case_id", sorted(EXPECTED))
def test_case_classification_is_pinned(case_id):
    case = next(case for case in CASES if case.case_id == case_id)
    result = run_case(case)
    assert result["classification"] in {"caught", "missed"}
    assert result["classification"] == EXPECTED[case_id], result


def test_catalog_is_deterministic():
    assert build_catalog() == build_catalog()


def test_committed_results_match_a_fresh_run():
    committed = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert committed == build_catalog()


def test_toctou_case_leaves_an_unverifiable_record_in_the_trusted_log():
    case = next(case for case in CASES if case.case_id == "dict_subclass_toctou")
    result = run_case(case)
    assert result["evidence"]["committed"] is True
    assert result["evidence"]["stored_output_reverifies"] is False
