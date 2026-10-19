from datetime import datetime, timezone

import pytest

from neural_node_swarm.memory import MemoryStore
from neural_node_swarm.sqlite_storage import SQLiteStorage


def output(step_id="step-1"):
    return {"schema_version": "1.0", "step_id": step_id, "objective": "Build: result", "success_criteria": ["done"], "required_memory_refs": []}


def fixed_clock():
    return datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)


def counter_ids(prefix="evt"):
    state = {"n": 0}

    def next_id():
        state["n"] += 1
        return f"{prefix}-{state['n']}"

    return next_id


def constant_ids():
    return "evt-same"


def jsonl_store(tmp_path, **kwargs):
    return MemoryStore(tmp_path / "events.jsonl", **kwargs)


def sqlite_store(tmp_path, **kwargs):
    return MemoryStore(storage=SQLiteStorage(tmp_path / "events.db"), **kwargs)


BACKENDS = [jsonl_store, sqlite_store]


@pytest.mark.parametrize("make_store", BACKENDS)
def test_injected_id_source_and_clock_are_used(tmp_path, make_store):
    store = make_store(tmp_path, id_source=counter_ids(), clock=fixed_clock)
    first = store.append(node_id="node-1", round=1, objective="Build: result", output=output("step-1"))
    second = store.append(node_id="node-1", round=2, objective="Build: result", output=output("step-2"))
    assert [first["event_id"], second["event_id"]] == ["evt-1", "evt-2"]
    assert first["recorded_at"] == second["recorded_at"] == "2026-09-05T12:00:00+00:00"
    assert [event["event_id"] for event in store.events()] == ["evt-1", "evt-2"]


@pytest.mark.parametrize("make_store", BACKENDS)
def test_default_ids_are_distinct_within_one_clock_tick(tmp_path, make_store):
    store = make_store(tmp_path, clock=fixed_clock)
    first = store.append(node_id="node-1", round=1, objective="Build: result", output=output("step-1"))
    second = store.append(node_id="node-1", round=2, objective="Build: result", output=output("step-2"))
    assert first["event_id"] != second["event_id"]
    assert len({event["event_id"] for event in store.events()}) == 2


@pytest.mark.parametrize("make_store", BACKENDS)
def test_duplicate_event_id_is_rejected_and_not_stored(tmp_path, make_store):
    store = make_store(tmp_path, id_source=constant_ids, clock=fixed_clock)
    store.append(node_id="node-1", round=1, objective="Build: result", output=output("step-1"))
    with pytest.raises(ValueError, match="duplicate event_id"):
        store.append(node_id="node-1", round=2, objective="Build: result", output=output("step-2"))
    events = store.events()
    assert len(events) == 1
    assert events[0]["step_id"] == "step-1"
