from neural_node_swarm import MemoryStore, Orchestrator
from neural_node_swarm.node import DisposableNode


def test_reference_node_completes_three_rounds(tmp_path):
    store = MemoryStore(tmp_path / "events.jsonl")
    final = Orchestrator(store, DisposableNode("node-1").fire).run("begin")
    assert final["step_id"] == "node-1:3"
    assert len(store.events()) == 3


def test_injected_failure_is_not_persisted(tmp_path):
    store = MemoryStore(tmp_path / "events.jsonl")
    try:
        Orchestrator(store, DisposableNode("node-1", fail_round=2).fire).run("begin")
    except ValueError:
        pass
    assert len(store.events()) == 1
