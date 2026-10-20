import json

import pytest

from neural_node_swarm.poisoning_experiment import multi_hop_experiment, run_multi_hop


def _outcomes(result):
    return [g["outcome"] for g in result["per_generation"]]


def test_promotion_contains_injection_at_depth(tmp_path):
    result = run_multi_hop(tmp_path, policy="promotion", inject_at=3, successors=5)
    assert _outcomes(result) == ["correct"] * 5
    assert result["metrics"] == {"wrong_successor_reads": 0, "correct_successor_reads": 5,
                                 "abstentions": 0, "first_wrong_generation": None}
    assert result["evidence_checks"] == 5


def test_shared_gated_abstains_after_injection(tmp_path):
    result = run_multi_hop(tmp_path, policy="shared_gated", inject_at=3, successors=5)
    assert _outcomes(result) == ["correct", "correct", "correct", "abstain", "abstain"]
    assert result["metrics"] == {"wrong_successor_reads": 0, "correct_successor_reads": 3,
                                 "abstentions": 2, "first_wrong_generation": None}
    assert result["evidence_checks"] == 5


@pytest.mark.parametrize("policy,expected", [
    ("promotion", ["correct", "correct", "correct", "wrong", "wrong"]),
    # Read-time checks see the corrupted record at the injection generation itself.
    ("shared_gated", ["correct", "correct", "abstain", "wrong", "wrong"]),
])
def test_corrupted_evidence_propagates_past_injection_depth(tmp_path, policy, expected):
    result = run_multi_hop(tmp_path, policy=policy, inject_at=3, successors=5, evidence_fault=True)
    assert _outcomes(result) == expected
    assert result["metrics"]["first_wrong_generation"] == 4
    assert result["evidence_checks"] == 5


def test_successors_run_through_orchestrator(tmp_path):
    run_multi_hop(tmp_path, policy="promotion", inject_at=2, successors=3)
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert len(events) == 9
    assert [e["node_id"] for e in events[::3]] == ["node-1", "node-2", "node-3"]


def test_experiment_matches_check_cost_and_states_limits(tmp_path):
    report = multi_hop_experiment(tmp_path, inject_at=3, successors=5)
    cases = {(c["scenario"], c["policy"]): c for c in report["cases"]}
    assert set(cases) == {(s, p) for s in ("poison", "poison_and_corrupt_evidence")
                          for p in ("promotion", "shared_gated")}
    for scenario in ("poison", "poison_and_corrupt_evidence"):
        assert cases[(scenario, "promotion")]["evidence_checks"] == \
            cases[(scenario, "shared_gated")]["evidence_checks"] == 5
    assert report["matched_check_cost"] is True
    assert cases[("poison", "shared_gated")]["metrics"]["abstentions"] == 2
    assert isinstance(report["does_not_show"], list) and len(report["does_not_show"]) >= 3
    assert all(isinstance(item, str) and item for item in report["does_not_show"])


@pytest.mark.parametrize("kwargs", [{"policy": "other", "inject_at": 2},
                                    {"policy": "promotion", "inject_at": 1},
                                    {"policy": "promotion", "inject_at": 7},
                                    {"policy": "promotion", "inject_at": True}])
def test_rejects_bad_arguments(tmp_path, kwargs):
    with pytest.raises(ValueError):
        run_multi_hop(tmp_path, successors=6, **kwargs)
