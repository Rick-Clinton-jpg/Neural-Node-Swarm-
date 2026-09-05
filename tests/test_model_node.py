from neural_node_swarm.nodes import ModelNode


class FakeClient:
    def complete(self, *, objective, memory, round_number):
        return {"schema_version": "1.0", "step_id": f"model:{round_number}", "objective": f"next: {objective}", "success_criteria": ["next objective is schema-valid"], "required_memory_refs": []}


def test_model_node_injects_client_and_memory_reader():
    seen = []
    node = ModelNode("node-model", FakeClient(), lambda refs: seen.append(refs) or [{"event_id": "e1"}])
    result = node.fire({"objective": "start", "required_memory_refs": ["episodic:latest"]}, 1)
    assert result["step_id"] == "model:1"
    assert seen == [["episodic:latest"]]
