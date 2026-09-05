import json

import pytest

from neural_node_swarm.memory import MemoryStore
from neural_node_swarm.verifier import verify_node_output


def valid_output():
    return {"schema_version": "1.0", "step_id": "step-1", "objective": "Produce a verified result", "success_criteria": ["The result is schema-valid"], "required_memory_refs": ["episodic:latest"]}


def test_valid_output_is_accepted():
    assert verify_node_output(valid_output())["status"] == "passed"


@pytest.mark.parametrize("field", ["notes", "reasoning", "raw_context"])
def test_free_form_side_channels_are_rejected(field):
    output = valid_output()
    output[field] = "smuggled state"
    assert verify_node_output(output)["status"] == "rejected"


def test_failed_output_never_enters_memory(tmp_path):
    store = MemoryStore(tmp_path / "episodic.jsonl")
    output = valid_output()
    output["confidence"] = 2
    with pytest.raises(ValueError):
        store.append(node_id="node-1", round=1, objective="x", output=output)
    assert store.events() == []


def test_memory_is_append_only_and_distillation_has_provenance(tmp_path):
    store = MemoryStore(tmp_path / "episodic.jsonl")
    store.append(node_id="node-1", round=1, objective="x", output=valid_output())
    events = store.events()
    distilled = store.distilled()
    assert len(events) == 1
    assert distilled["source_event_ids"] == [events[0]["event_id"]]
    json.loads((tmp_path / "episodic.jsonl").read_text())


def test_memory_refs_are_explicit_and_read_only(tmp_path):
    store = MemoryStore(tmp_path / "episodic.jsonl")
    event = store.append(node_id="node-1", round=1, objective="x", output=valid_output())
    assert store.read_refs(["episodic:latest"])[0]["event_id"] == event["event_id"]
    assert store.read_refs([f"episodic:event:{event['event_id']}"])[0]["step_id"] == "step-1"
    with pytest.raises(KeyError):
        store.read_refs(["episodic:event:missing"])
