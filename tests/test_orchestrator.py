from neural_node_swarm import MemoryStore, Orchestrator


def test_relay_runs_exactly_three_rounds(tmp_path):
    calls = []

    def node(current, round_number):
        calls.append((current["objective"], round_number))
        return {"schema_version": "1.0", "step_id": f"step-{round_number}", "objective": f"objective-{round_number}", "success_criteria": ["next objective is schema-valid"], "required_memory_refs": []}

    final = Orchestrator(MemoryStore(tmp_path / "events.jsonl"), node).run("start")
    assert len(calls) == 3
    assert len(MemoryStore(tmp_path / "events.jsonl").events()) == 3
    assert final["objective"] == "objective-3"


def test_failed_round_stops_before_commit(tmp_path):
    def node(current, round_number):
        return {"schema_version": "1.0", "step_id": "bad", "objective": "x", "success_criteria": [], "required_memory_refs": []}

    store = MemoryStore(tmp_path / "events.jsonl")
    try:
        Orchestrator(store, node).run("start")
    except ValueError:
        pass
    assert store.events() == []


def test_successor_rotation_is_non_overlapping_and_fresh(tmp_path):
    store = MemoryStore(tmp_path / "events.jsonl")
    instances = []

    def factory(node_id):
        token = object()
        instances.append((node_id, token))
        def fire(current, round_number):
            return {"schema_version": "1.0", "step_id": f"{node_id}:{round_number}", "objective": f"from-{node_id}", "success_criteria": ["next objective is schema-valid"], "required_memory_refs": ["episodic:latest"]}
        return fire

    final = __import__("neural_node_swarm").Orchestrator(store, lambda *_: {}).run_chain("start", node_factory=factory, node_count=2)
    assert [item[0] for item in instances] == ["node-1", "node-2"]
    assert len(store.events()) == 6
    assert final["step_id"] == "node-2:3"
