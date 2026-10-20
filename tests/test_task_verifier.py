import pytest

from neural_node_swarm.memory import MemoryStore
from neural_node_swarm.orchestrator import Orchestrator
from neural_node_swarm.task_verifier import SumTask, verify_task_output
from neural_node_swarm.verifier import verify_node_output

TASK = SumTask(numbers=(3, -7, 12, 40, 0, 5))  # ground truth 53


def answer_output(objective, step_id="step-3"):
    return {
        "schema_version": "1.0",
        "step_id": step_id,
        "objective": objective,
        "success_criteria": ["answer matches ground truth"],
        "required_memory_refs": [],
    }


def test_ground_truth_is_the_sum():
    assert TASK.ground_truth() == 53


def test_task_rejects_non_integer_numbers():
    with pytest.raises(ValueError):
        SumTask(numbers=(1, 2.5))
    with pytest.raises(ValueError):
        SumTask(numbers=(1, True))
    with pytest.raises(ValueError):
        SumTask(numbers=())


def test_correct_answer_passes():
    result = verify_task_output(answer_output("ANSWER: 53"), TASK)
    assert result["status"] == "passed"
    assert result["expected"] == 53
    assert result["observed"] == 53


@pytest.mark.parametrize(
    "objective",
    [
        "ANSWER: 52",  # off by one below
        "ANSWER: 54",  # off by one above
        "ANSWER: -53",  # wrong sign
        "ANSWER: 0",  # wrong
        "Report the sum of the list",  # missing answer
        "ANSWER:",  # empty answer
        "ANSWER: 53.0",  # not an integer
        "ANSWER: 53 or 54",  # hedged
        "answer: 53",  # wrong marker case
        "The ANSWER: 53",  # marker not at start
        "ANSWER: 053",  # non-canonical integer
        "ANSWER: +53",  # non-canonical integer
    ],
)
def test_well_formed_but_wrong_outputs_are_rejected(objective):
    output = answer_output(objective)
    assert verify_node_output(output)["status"] == "passed"
    result = verify_task_output(output, TASK)
    assert result["status"] == "rejected"


def test_malformed_output_is_rejected_even_with_right_answer():
    output = answer_output("ANSWER: 53")
    output["notes"] = "side channel"
    assert verify_node_output(output)["status"] == "rejected"
    result = verify_task_output(output, TASK)
    assert result["status"] == "rejected"
    assert result["observed"] is None


def test_non_object_is_rejected():
    assert verify_task_output("ANSWER: 53", TASK)["status"] == "rejected"


def test_verifier_is_deterministic():
    output = answer_output("ANSWER: 53")
    assert verify_task_output(output, TASK) == verify_task_output(output, TASK)


def _relay_factory(final_answer):
    def node(current, round_number):
        if round_number < 3:
            return answer_output(f"Sum the list, step {round_number}", step_id=f"step-{round_number}")
        return answer_output(f"ANSWER: {final_answer}", step_id="step-3")

    return node


def test_relay_final_output_is_judged_against_ground_truth(tmp_path):
    good = Orchestrator(MemoryStore(tmp_path / "good.jsonl"), _relay_factory(53)).run(TASK.prompt())
    bad = Orchestrator(MemoryStore(tmp_path / "bad.jsonl"), _relay_factory(54)).run(TASK.prompt())
    assert verify_node_output(good)["status"] == "passed"
    assert verify_node_output(bad)["status"] == "passed"
    assert verify_task_output(good, TASK)["status"] == "passed"
    assert verify_task_output(bad, TASK)["status"] == "rejected"


def test_prompt_carries_the_numbers():
    prompt = TASK.prompt()
    assert "3, -7, 12, 40, 0, 5" in prompt
    assert "ANSWER:" in prompt
