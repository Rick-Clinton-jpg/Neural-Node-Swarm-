"""validate-on-read: a schema-valid edit to a stored record must still fail closed on read."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from neural_node_swarm.memory import MemoryStore
from neural_node_swarm.sqlite_storage import SQLiteStorage
from neural_node_swarm.verifier import VerificationError


def _output(step_id):
    return {
        "schema_version": "1.0",
        "step_id": step_id,
        "objective": "Build: result",
        "success_criteria": ["result is correct"],
        "required_memory_refs": ["episodic:latest"],
    }


def _deterministic(start_index=1):
    counter = iter(range(start_index, 1000))
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ticks = iter(range(start_index, 1000))
    return (lambda: f"evt-{next(counter)}", lambda: start + timedelta(seconds=next(ticks)))


def _jsonl_store(tmp_path, start_index=1):
    ids, clock = _deterministic(start_index)
    return MemoryStore(tmp_path / "events.jsonl", id_source=ids, clock=clock)


def _sqlite_store(tmp_path, start_index=1):
    ids, clock = _deterministic(start_index)
    return MemoryStore(storage=SQLiteStorage(tmp_path / "events.db"), id_source=ids, clock=clock)


def _fill(store, count=3):
    return [
        store.append(node_id="node-1", round=index, objective="Build: result", output=_output(f"s{index}"))
        for index in range(1, count + 1)
    ]


# --- raw access to stored records, bypassing MemoryStore ----------------------


def _jsonl_read(store):
    return [json.loads(line) for line in store.path.read_text(encoding="utf-8").splitlines() if line]


def _jsonl_write(store, records):
    store.path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in records), encoding="utf-8")


def _jsonl_drop_chain(store):
    store.path.with_name(store.path.name + ".chain").unlink()


def _sqlite_read(store):
    with sqlite3.connect(store.storage.path) as connection:
        return [json.loads(row[0]) for row in connection.execute("SELECT payload FROM events ORDER BY rowid")]


def _sqlite_write(store, records):
    with sqlite3.connect(store.storage.path) as connection:
        connection.execute("DELETE FROM events")
        for record in records:
            connection.execute(
                "INSERT INTO events(event_id, recorded_at, payload) VALUES (?, ?, ?)",
                (record["event_id"], record["recorded_at"], json.dumps(record, sort_keys=True)),
            )


def _sqlite_drop_chain(store):
    with sqlite3.connect(store.storage.path) as connection:
        connection.execute("DELETE FROM event_chain")


BACKENDS = {
    "jsonl": (_jsonl_store, _jsonl_read, _jsonl_write, _jsonl_drop_chain),
    "sqlite": (_sqlite_store, _sqlite_read, _sqlite_write, _sqlite_drop_chain),
}


@pytest.fixture(params=sorted(BACKENDS))
def backend(request):
    return BACKENDS[request.param]


def test_untampered_records_read_back_after_reopen(tmp_path, backend):
    make, read, _, _ = backend
    written = _fill(make(tmp_path))
    reopened = make(tmp_path, start_index=100)
    assert reopened.events() == written
    assert reopened.read_refs(["episodic:latest", "episodic:event:evt-1"]) == [written[-1], written[0]]
    more = reopened.append(node_id="node-2", round=3, objective="Build: result", output=_output("s4"))
    assert make(tmp_path, start_index=200).events() == written + [more]
    assert read(reopened) == written + [more]


def _objective(record):
    record["output"]["objective"] = "Advance: forged but valid"


def _top_objective(record):
    record["objective"] = "Advance: forged but valid"


def _node_id(record):
    record["node_id"] = "node-evil"


def _recorded_at(record):
    record["recorded_at"] = "2030-01-01T00:00:00+00:00"


@pytest.mark.parametrize("mutate", [_objective, _top_objective, _node_id, _recorded_at])
@pytest.mark.parametrize("index", [0, 1, 2])
def test_schema_valid_edit_fails_closed(tmp_path, backend, mutate, index):
    make, read, write, _ = backend
    store = make(tmp_path)
    _fill(store)
    records = read(store)
    mutate(records[index])
    write(store, records)
    # The edited record is still a valid, verifier-consistent memory event on its own.
    from neural_node_swarm.memory import validate_event

    validate_event(records[index])
    with pytest.raises(VerificationError):
        store.events()
    with pytest.raises(VerificationError):
        store.read_refs(["episodic:latest"])
    with pytest.raises(VerificationError):
        store.read_refs([f"episodic:event:{records[index]['event_id']}"])


@pytest.mark.parametrize("index", [0, 1, 2])
def test_deleted_record_fails_closed(tmp_path, backend, index):
    make, read, write, _ = backend
    store = make(tmp_path)
    _fill(store)
    records = read(store)
    del records[index]
    write(store, records)
    with pytest.raises(VerificationError):
        store.events()


def test_swapped_records_fail_closed(tmp_path, backend):
    make, read, write, _ = backend
    store = make(tmp_path)
    _fill(store)
    records = read(store)
    records[0], records[1] = records[1], records[0]
    write(store, records)
    with pytest.raises(VerificationError):
        store.events()


def test_duplicated_record_fails_closed(tmp_path):
    store = _jsonl_store(tmp_path)
    _fill(store)
    records = _jsonl_read(store)
    _jsonl_write(store, records + [records[-1]])
    with pytest.raises(VerificationError):
        store.events()


def test_missing_chain_fails_closed(tmp_path, backend):
    make, _, _, drop_chain = backend
    store = make(tmp_path)
    _fill(store)
    drop_chain(store)
    with pytest.raises(VerificationError):
        store.events()
    with pytest.raises(VerificationError):
        store.read_refs(["episodic:latest"])
