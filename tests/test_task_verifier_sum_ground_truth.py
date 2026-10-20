import pytest

from neural_node_swarm import MemoryStore, Orchestrator
from neural_node_swarm.node import DisposableNode
from neural_node_swarm.task_verifier import expected_answer, sum_objective, verify_relay, verify_task_output
from neural_node_swarm.verifier import verify_node_output

START = "Sum: 2,3,5"


def output(objective, step_id="step-3"):
    return {"schema_version": "1.0", "step_id": step_id, "objective": objective, "success_criteria": ["answer is exact"], "required_memory_refs": ["episodic:latest"]}


def check(result, name):
    return next(c for c in result["checks"] if c["name"] == name)


def test_task_has_known_ground_truth():
    assert sum_objective([2, 3, 5]) == START
    assert expected_answer(START) == "Answer: 10"
    assert expected_answer(sum_objective([-4, 1])) == "Answer: -3"


@pytest.mark.parametrize("bad", ["", "Sum:", "Sum: 2,,5", "Sum: two", "Total: 2,3", "Sum: 2,3 "])
def test_malformed_start_objective_is_refused(bad):
    with pytest.raises(ValueError):
        expected_answer(bad)


def test_correct_relay_through_orchestrator_passes(tmp_path):
    def node(current, round_number):
        if round_number < 3:
            return output(current["objective"], step_id=f"step-{round_number}")
        return output(expected_answer(START), step_id="step-3")

    store = MemoryStore(tmp_path / "events.jsonl")
    final = Orchestrator(store, node).run(START)
    result = verify_relay(START, final)
    assert result["status"] == "passed"
    assert check(result, "task_answer")["passed"] is True
    assert len(store.events()) == 3


@pytest.mark.parametrize(
    "objective",
    [
        "Answer: 11",  # wrong sum
        "Answer: 9",  # off by one
        "Answer: 10 because 2+3+5",  # extra text after the answer
        "Answer: 12",  # correct answer for different numbers (2,3,7)
    ],
)
def test_well_formed_but_wrong_outputs_are_rejected(objective):
    candidate = output(objective)
    assert verify_node_output(candidate)["status"] == "passed"
    result = verify_task_output(START, candidate)
    assert result["status"] == "rejected"
    assert check(result, "node_output")["passed"] is True
    assert check(result, "task_answer")["passed"] is False


def test_correct_answer_for_different_numbers_is_rejected():
    other = sum_objective([2, 3, 7])
    candidate = output(expected_answer(other))
    assert verify_node_output(candidate)["status"] == "passed"
    assert verify_task_output(other, candidate)["status"] == "passed"
    assert verify_task_output(START, candidate)["status"] == "rejected"


def test_disposable_node_relay_that_never_answers_is_rejected(tmp_path):
    final = Orchestrator(MemoryStore(tmp_path / "events.jsonl"), DisposableNode("node-1").fire).run(START)
    assert verify_node_output(final)["status"] == "passed"
    result = verify_relay(START, final)
    assert result["status"] == "rejected"
    assert check(result, "task_answer")["passed"] is False


def test_malformed_output_is_rejected():
    candidate = output("Answer: 10")
    candidate["notes"] = "smuggled"
    result = verify_task_output(START, candidate)
    assert result["status"] == "rejected"
    assert check(result, "node_output")["passed"] is False
    assert verify_task_output(START, "Answer: 10")["status"] == "rejected"


def test_verifier_is_deterministic():
    candidate = output("Answer: 10")
    assert verify_task_output(START, candidate) == verify_task_output(START, candidate)
    assert verify_task_output(START, candidate)["status"] == "passed"
