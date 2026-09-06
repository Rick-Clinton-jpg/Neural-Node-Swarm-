import pytest

from neural_node_swarm.poisoning_experiment import experiment, run_case


def test_poison_is_active_but_not_inherited():
    shared = run_case("shared")
    governed = run_case("promotion")
    assert shared["active"] == governed["active"] == {"hand": 90, "leg": 20}
    assert [e["checks"] for e in shared["audit"]] == [e["checks"] for e in governed["audit"]]
    assert shared["metrics"]["wrong_successor_reads"] == 6
    assert governed["metrics"]["wrong_successor_reads"] == 0
    assert governed["metrics"]["correct_successor_reads"] == 6
    assert governed["metrics"]["abstentions"] == 6
    assert governed["audit"][0]["inherited"] is False


def test_clean_input_has_no_progress_penalty():
    assert run_case("shared", poisoned=False)["metrics"] == run_case("promotion", poisoned=False)["metrics"]


def test_corrupt_evidence_defeats_promotion():
    assert run_case("promotion", evidence_fault=True)["metrics"]["wrong_successor_reads"] == 6


def test_exact_replay_and_provenance():
    assert experiment() == experiment()
    for case in experiment()["cases"]:
        decisions = {e["event_id"]: e for e in case["audit"]}
        for state in case["durable"].values():
            assert decisions[state["decision_ref"]]["inherited"]


@pytest.mark.parametrize("count", [0, -1, True])
def test_invalid_run_length(count):
    with pytest.raises(ValueError):
        experiment(count)
