from datetime import datetime, timezone

import pytest

from neural_node_swarm.memory import MemoryStore
from neural_node_swarm.sqlite_storage import SQLiteStorage

FROZEN = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)


def frozen_clock():
    return FROZEN


def output(step_id):
    return {"schema_version": "1.0", "step_id": step_id, "objective": "Build: result", "success_criteria": ["done"], "required_memory_refs": []}


def make_store(backend, tmp_path, **kwargs):
    if backend == "jsonl":
        return MemoryStore(tmp_path / "memory.jsonl", **kwargs)
    return MemoryStore(storage=SQLiteStorage(tmp_path / "memory.db"), **kwargs)


BACKENDS = ["jsonl", "sqlite"]


@pytest.mark.parametrize("backend", BACKENDS)
def test_injected_id_source_and_clock_are_used(backend, tmp_path):
    ids = iter(["evt-a", "evt-b"])
    store = make_store(backend, tmp_path, id_source=lambda: next(ids), clock=frozen_clock)
    first = store.append(node_id="n", round=1, objective="Build: result", output=output("s1"))
    second = store.append(node_id="n", round=2, objective="Build: result", output=output("s2"))
    assert first["event_id"] == "evt-a"
    assert second["event_id"] == "evt-b"
    assert first["recorded_at"] == FROZEN.isoformat()
    assert second["recorded_at"] == FROZEN.isoformat()
    stored = store.events()
    assert [e["event_id"] for e in stored] == ["evt-a", "evt-b"]
    assert all(e["recorded_at"] == FROZEN.isoformat() for e in stored)


@pytest.mark.parametrize("backend", BACKENDS)
def test_default_id_source_is_distinct_under_frozen_clock(backend, tmp_path):
    store = make_store(backend, tmp_path, clock=frozen_clock)
    first = store.append(node_id="n", round=1, objective="Build: result", output=output("s1"))
    second = store.append(node_id="n", round=2, objective="Build: result", output=output("s2"))
    assert first["event_id"] != second["event_id"]
    assert len({e["event_id"] for e in store.events()}) == 2


@pytest.mark.parametrize("backend", BACKENDS)
def test_duplicate_event_id_is_rejected_and_not_stored(backend, tmp_path):
    store = make_store(backend, tmp_path, id_source=lambda: "evt-same", clock=frozen_clock)
    store.append(node_id="n", round=1, objective="Build: result", output=output("s1"))
    with pytest.raises(ValueError):
        store.append(node_id="n", round=2, objective="Build: result", output=output("s2"))
    stored = store.events()
    assert len(stored) == 1
    assert stored[0]["step_id"] == "s1"


def test_sqlite_storage_append_event_raises_value_error_on_duplicate(tmp_path):
    storage = SQLiteStorage(tmp_path / "memory.db")
    event = {"event_id": "e1", "recorded_at": FROZEN.isoformat()}
    storage.append_event(event)
    with pytest.raises(ValueError):
        storage.append_event(dict(event))
    assert len(storage.list_events()) == 1
