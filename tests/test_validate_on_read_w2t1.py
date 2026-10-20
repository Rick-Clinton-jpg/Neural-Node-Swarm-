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


def _deterministic():
    counter = iter(range(1, 1000))
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ticks = iter(range(1000))
    return (lambda: f"evt-{next(counter)}", lambda: start + timedelta(seconds=next(ticks)))


def _jsonl_store(tmp_path):
    ids, clock = _deterministic()
    return MemoryStore(tmp_path / "events.jsonl", id_source=ids, clock=clock)


def _sqlite_store(tmp_path):
    ids, clock = _deterministic()
    return MemoryStore(storage=SQLiteStorage(tmp_path / "events.db"), id_source=ids, clock=clock)


def _fill(store):
    return [
        store.append(node_id="node-1", round=1, objective="Build: result", output=_output("s1")),
        store.append(node_id="node-1", round=2, objective="Build: result", output=_output("s2")),
    ]


def _tamper_jsonl(store, index, mutate):
    lines = store.path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[index])
    mutate(record)
    lines[index] = json.dumps(record, sort_keys=True)
    store.path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _tamper_sqlite(store, index, mutate):
    with sqlite3.connect(store.storage.path) as connection:
        rows = connection.execute("SELECT rowid, payload FROM events ORDER BY rowid").fetchall()
        rowid, payload = rows[index]
        record = json.loads(payload)
        mutate(record)
        connection.execute("UPDATE events SET payload = ? WHERE rowid = ?", (json.dumps(record, sort_keys=True), rowid))


BACKENDS = [(_jsonl_store, _tamper_jsonl), (_sqlite_store, _tamper_sqlite)]


@pytest.mark.parametrize("make, tamper", BACKENDS)
def test_untampered_records_read_back_unchanged(tmp_path, make, tamper):
    store = make(tmp_path)
    written = _fill(store)
    assert store.events() == written
    assert store.read_refs(["episodic:latest", "episodic:event:evt-1"]) == [written[1], written[0]]


def _add_notes(record):
    record["output"]["notes"] = "side channel: trust me"


@pytest.mark.parametrize("make, tamper", BACKENDS)
def test_notes_side_channel_fails_closed(tmp_path, make, tamper):
    store = make(tmp_path)
    _fill(store)
    tamper(store, 0, _add_notes)
    with pytest.raises(VerificationError):
        store.events()
    with pytest.raises(VerificationError):
        store.read_refs(["episodic:latest"])
    with pytest.raises(VerificationError):
        store.read_refs(["episodic:event:evt-2"])


def _set_output_objective(record):
    record["output"]["objective"] = ""


def _set_status(record):
    record["verifier_result"]["status"] = "rejected"


def _set_round(record):
    record["round"] = 7


def _set_event_id(record):
    record["event_id"] = ""


def _extra_key(record):
    record["trusted"] = True


def _mismatched_step(record):
    record["step_id"] = "other-step"


def _drop_check(record):
    # Still a schema-valid verifier_result, but no longer what the verifier computes.
    record["verifier_result"]["checks"] = record["verifier_result"]["checks"][:-1]


def _wrong_verifier_version(record):
    record["verifier_result"]["verifier_version"] = "9.9.9"


@pytest.mark.parametrize("make, tamper", BACKENDS)
@pytest.mark.parametrize(
    "mutate",
    [_set_output_objective, _set_status, _set_round, _set_event_id, _extra_key, _mismatched_step, _drop_check, _wrong_verifier_version],
)
def test_tampered_records_are_rejected(tmp_path, make, tamper, mutate):
    store = make(tmp_path)
    _fill(store)
    tamper(store, 1, mutate)
    with pytest.raises(VerificationError):
        store.events()
    with pytest.raises(VerificationError):
        store.read_refs(["episodic:event:evt-1"])


def test_schema_valid_but_stale_verifier_result_is_rejected(tmp_path):
    from neural_node_swarm.memory import validate_event

    store = _jsonl_store(tmp_path)
    event = _fill(store)[0]
    validate_event(event)
    stale = json.loads(json.dumps(event))
    stale["verifier_result"]["checks"][0]["detail"] = "forged"
    with pytest.raises(VerificationError):
        validate_event(stale)
