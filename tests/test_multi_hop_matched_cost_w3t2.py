import pytest

from neural_node_swarm import MemoryStore
from neural_node_swarm.multi_hop_matched_cost import POLICIES, SCENARIOS, compare, run_multi_hop
from neural_node_swarm.verifier import verify_node_output

GENERATIONS = 6
INJECT_AT = 3


def _run(tmp_path, policy, scenario, **kwargs):
    memory = MemoryStore(tmp_path / f"{policy}-{scenario}.jsonl")
    result = run_multi_hop(tmp_path / f"{policy}-{scenario}.db", memory, policy=policy,
                           scenario=scenario, **kwargs)
    return result, memory


def _hand(result):
    return {r["generation"]: r["value"] for r in result["reads"] if r["region"] == "hand"}


def _leg(result):
    return {r["generation"]: r["value"] for r in result["reads"] if r["region"] == "leg"}


def _check_memory(memory, generations=GENERATIONS):
    events = memory.events()
    assert len(events) == 3 * generations
    for index, event in enumerate(events):
        assert event["node_id"] == f"node-{index // 3 + 1}"
        assert event["round"] == index % 3 + 1
        assert verify_node_output(event["output"])["status"] == "passed"


def test_shared_poisoned_contaminates_every_generation_after_injection(tmp_path):
    result, memory = _run(tmp_path, "shared", "poisoned")
    hand = _hand(result)
    assert sorted(hand) == list(range(1, GENERATIONS + 1))
    assert all(hand[g] == 10 for g in range(1, INJECT_AT + 1))
    assert all(hand[g] == 90 for g in range(INJECT_AT + 1, GENERATIONS + 1))
    assert all(v == 20 for v in _leg(result).values())
    assert result["first_contaminated_generation"] == INJECT_AT + 1
    assert result["metrics"]["wrong"] == GENERATIONS - INJECT_AT
    assert result["evidence_checks"] == 0
    _check_memory(memory)


@pytest.mark.parametrize("policy", ["promotion", "gated_shared"])
def test_gated_policies_block_poisoned_write(tmp_path, policy):
    result, memory = _run(tmp_path, policy, "poisoned")
    assert result["metrics"]["wrong"] == 0
    assert all(v == 10 for v in _hand(result).values())
    assert result["first_contaminated_generation"] is None
    _check_memory(memory)


@pytest.mark.parametrize("policy", ["promotion", "gated_shared"])
def test_gated_policies_contaminated_by_corrupted_evidence(tmp_path, policy):
    result, memory = _run(tmp_path, policy, "corrupted")
    hand = _hand(result)
    assert all(hand[g] == 10 for g in range(1, INJECT_AT + 1))
    assert all(hand[g] == 90 for g in range(INJECT_AT + 1, GENERATIONS + 1))
    assert result["first_contaminated_generation"] == INJECT_AT + 1
    assert result["metrics"]["wrong"] == GENERATIONS - INJECT_AT
    _check_memory(memory)


def test_clean_scenario_all_correct(tmp_path):
    for policy in POLICIES:
        result, _ = _run(tmp_path, policy, "clean")
        assert result["metrics"] == {"wrong": 0, "correct": 2 * GENERATIONS, "abstentions": 0}
        assert result["first_contaminated_generation"] is None


def test_matched_cost_and_reporting(tmp_path):
    for scenario in SCENARIOS:
        promo, _ = _run(tmp_path, "promotion", scenario)
        gated, _ = _run(tmp_path, "gated_shared", scenario)
        assert promo["evidence_checks"] == gated["evidence_checks"] > 0
    result, _ = _run(tmp_path, "promotion", "poisoned")
    assert "abstentions" in result["metrics"]
    assert result["does_not_show"] and all(isinstance(s, str) and s for s in result["does_not_show"])


def test_successors_record_reads_and_proposal_in_memory(tmp_path):
    result, memory = _run(tmp_path, "shared", "poisoned")
    outputs = [e["output"] for e in memory.events()]
    round_one = [o for o in outputs if o["step_id"].endswith(":1")]
    assert "read:hand=10" in round_one[0]["constraints"]
    assert "read:hand=90" in round_one[INJECT_AT]["constraints"]
    proposals = [o for o in outputs if any(c.startswith("propose:") for c in o.get("constraints", []))]
    assert [o["step_id"] for o in proposals] == [f"node-{INJECT_AT}:3"]
    assert result["proposals"] == [{"generation": INJECT_AT, "region": "hand", "value": 90,
                                    "evidence_ref": "record:hand", "applied": True}]


def test_injection_depth_is_configurable(tmp_path):
    result, _ = _run(tmp_path, "shared", "poisoned", generations=8, inject_at=5)
    assert result["first_contaminated_generation"] == 6
    assert result["metrics"]["wrong"] == 3


def test_compare_runs_every_case_and_reports_matched_cost(tmp_path):
    report = compare(tmp_path)
    cases = {(c["policy"], c["scenario"]) for c in report["cases"]}
    assert cases == {(p, s) for p in POLICIES for s in SCENARIOS}
    for scenario in SCENARIOS:
        entry = report["matched_cost"][scenario]
        assert entry["equal"] is True
        assert entry["promotion"] == entry["gated_shared"]
    assert report["does_not_show"]
    assert all("abstentions" in c["metrics"] for c in report["cases"])


def test_invalid_arguments(tmp_path):
    memory = MemoryStore(tmp_path / "e.jsonl")
    with pytest.raises(ValueError):
        run_multi_hop(tmp_path / "x.db", memory, policy="nope", scenario="clean")
    with pytest.raises(ValueError):
        run_multi_hop(tmp_path / "x.db", memory, policy="shared", scenario="nope")
    for generations, inject_at in ((0, 1), (3, 3), (3, 0), (True, 1)):
        with pytest.raises(ValueError):
            run_multi_hop(tmp_path / "x.db", memory, policy="shared", scenario="poisoned",
                          generations=generations, inject_at=inject_at)
