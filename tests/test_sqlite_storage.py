import pytest

from neural_node_swarm.sqlite_storage import SQLiteStorage


def event(event_id):
    return {"event_id": event_id, "recorded_at": "2026-09-05T00:00:00+00:00", "payload": {"value": event_id}}


def test_sqlite_storage_persists_and_orders_events(tmp_path):
    path = tmp_path / "memory.db"
    store = SQLiteStorage(path)
    store.append_event(event("e1"))
    store.append_event(event("e2"))
    assert [item["event_id"] for item in store.list_events()] == ["e1", "e2"]
    assert store.get_event("e1")["payload"]["value"] == "e1"
    assert store.get_event("missing") is None
    reopened = SQLiteStorage(path)
    assert len(reopened.list_events()) == 2


def test_sqlite_storage_rejects_duplicate_event_ids(tmp_path):
    store = SQLiteStorage(tmp_path / "memory.db")
    store.append_event(event("e1"))
    with pytest.raises(Exception):
        store.append_event(event("e1"))
