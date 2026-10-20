import json

import pytest

from neural_node_swarm.poisoning_experiment import governed_runtime_experiment, run_governed_runtime


def _metrics(tmp_path, **kwargs):
    return run_governed_runtime(tmp_path, **kwargs)["metrics"]


def test_clean_control_inherits_truth_everywhere(tmp_path):
    result = run_governed_runtime(tmp_path, poisoned=False, evidence_fault=False, successors=3)
    assert result["policy"] == "governed_runtime"
    assert result["metrics"] == {"wrong_successor_reads": 0, "correct_successor_reads": 6,
                                 "abstentions": 0, "contaminated_regions": 0}
    assert [r["value"] for r in result["reads"] if r["region"] == "hand"] == [10, 10, 10]


def test_poisoned_hand_abstains_and_leg_is_correct(tmp_path):
    result = run_governed_runtime(tmp_path, poisoned=True, evidence_fault=False, successors=3)
    assert result["metrics"] == {"wrong_successor_reads": 0, "correct_successor_reads": 3,
                                 "abstentions": 3, "contaminated_regions": 0}
    hand = [r for r in result["reads"] if r["region"] == "hand"]
    assert [r["value"] for r in hand] == [None, None, None]
    assert [r["node_id"] for r in hand] == ["node-1", "node-2", "node-3"]


def test_corrupted_evidence_contaminates_hand_and_is_reported(tmp_path):
    metrics = _metrics(tmp_path, poisoned=True, evidence_fault=True, successors=3)
    assert metrics == {"wrong_successor_reads": 3, "correct_successor_reads": 3,
                       "abstentions": 0, "contaminated_regions": 1}


def test_successors_run_through_orchestrator_and_log_episodes(tmp_path):
    result = run_governed_runtime(tmp_path, poisoned=True, successors=2)
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    # two regions x two successors x three rounds, each committed by the Orchestrator
    assert len(events) == 12
    assert result["final_step_ids"] == {"hand": "node-2:3", "leg": "node-2:3"}


def test_experiment_reports_all_three_controls(tmp_path):
    report = governed_runtime_experiment(tmp_path, successors=2)
    by_name = {case["scenario"]: case["metrics"] for case in report["cases"]}
    assert set(by_name) == {"clean", "poison", "poison_and_corrupt_evidence"}
    assert by_name["clean"]["abstentions"] == 0
    assert by_name["poison"]["abstentions"] == 2
    assert by_name["poison"]["wrong_successor_reads"] == 0
    assert by_name["poison_and_corrupt_evidence"]["wrong_successor_reads"] == 2
    assert by_name["poison_and_corrupt_evidence"]["contaminated_regions"] == 1


def test_rejects_non_positive_successors(tmp_path):
    with pytest.raises(ValueError):
        run_governed_runtime(tmp_path, successors=0)
