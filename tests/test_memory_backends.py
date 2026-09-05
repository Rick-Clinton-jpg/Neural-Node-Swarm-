from neural_node_swarm.memory import MemoryStore
from neural_node_swarm.sqlite_storage import SQLiteStorage


def test_memory_store_can_switch_to_sqlite_backend(tmp_path):
    store = MemoryStore(storage=SQLiteStorage(tmp_path / "memory.db"))
    output = {"schema_version": "1.0", "step_id": "step-1", "objective": "Build: result", "success_criteria": ["done"], "required_memory_refs": []}
    store.append(node_id="node-1", round=1, objective="Build: result", output=output)
    assert store.events()[0]["step_id"] == "step-1"
    assert store.distilled()["patterns"][0]["key"] == "build"
