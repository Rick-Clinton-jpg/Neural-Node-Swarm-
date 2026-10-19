import json
import sqlite3
from datetime import datetime, timezone

import pytest

from neural_node_swarm.memory import MemoryStore, UntrustedMemoryError
from neural_node_swarm.sqlite_storage import SQLiteStorage


def output(step_id="step-1"):
    return {"schema_version": "1.0", "step_id": step_id, "objective": "Build: result", "success_criteria": ["done"], "required_memory_refs": []}


def fixed_clock():
    return datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)


def counter_ids():
    state = {"n": 0}

    def next_id():
        state["n"] += 1
        return f"evt-{state['n']}"

    return next_id


def jsonl_store(tmp_path):
    return MemoryStore(tmp_path / "events.jsonl", id_source=counter_ids(), clock=fixed_clock)


def sqlite_store(tmp_path):
    return MemoryStore(storage=SQLiteStorage(tmp_path / "events.db"), id_source=counter_ids(), clock=fixed_clock)


def tamper(tmp_path, store, index, mutate):
    """Rewrite one stored record in place, bypassing MemoryStore.append."""
    if store.storage is None:
        path = tmp_path / "events.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        record = json.loads(lines[index])
        mutate(record)
        lines[index] = json.dumps(record, sort_keys=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        connection = sqlite3.connect(tmp_path / "events.db")
        with connection:
            rows = connection.execute("SELECT rowid, payload FROM events ORDER BY rowid").fetchall()
            rowid, payload = rows[index]
            record = json.loads(payload)
            mutate(record)
            connection.execute("UPDATE events SET payload = ? WHERE rowid = ?", (json.dumps(record, sort_keys=True), rowid))
        connection.close()


def filled(tmp_path, make_store):
    store = make_store(tmp_path)
    store.append(node_id="node-1", round=1, objective="Build: result", output=output("step-1"))
    store.append(node_id="node-1", round=2, objective="Build: result", output=output("step-2"))
    return store


def add_notes(record):
    record["output"]["notes"] = "side channel for the successor"


def add_top_level_field(record):
    record["reasoning"] = "hidden scratchpad"


def break_round(record):
    record["round"] = 7


def blank_objective(record):
    record["output"]["objective"] = ""


def forge_verifier_pass(record):
    record["output"]["confidence"] = 5
    # leave the stored verifier_result claiming "passed"


def mismatch_step_id(record):
    record["step_id"] = "step-forged"


def rejected_verifier_result(record):
    record["verifier_result"]["status"] = "rejected"


TAMPERS = [add_notes, add_top_level_field, break_round, blank_objective, forge_verifier_pass, mismatch_step_id, rejected_verifier_result]


@pytest.mark.parametrize("make_store", [jsonl_store, sqlite_store])
def test_untampered_records_are_returned(tmp_path, make_store):
    store = filled(tmp_path, make_store)
    assert [event["step_id"] for event in store.events()] == ["step-1", "step-2"]
    assert store.read_refs(["episodic:latest"])[0]["step_id"] == "step-2"


@pytest.mark.parametrize("mutate", TAMPERS)
@pytest.mark.parametrize("make_store", [jsonl_store, sqlite_store])
def test_tampered_record_fails_closed_on_events(tmp_path, make_store, mutate):
    store = filled(tmp_path, make_store)
    tamper(tmp_path, store, 1, mutate)
    with pytest.raises(UntrustedMemoryError):
        store.events()


@pytest.mark.parametrize("mutate", TAMPERS)
@pytest.mark.parametrize("make_store", [jsonl_store, sqlite_store])
def test_tampered_record_fails_closed_on_read_refs(tmp_path, make_store, mutate):
    store = filled(tmp_path, make_store)
    tamper(tmp_path, store, 0, mutate)
    with pytest.raises(UntrustedMemoryError):
        store.read_refs(["episodic:event:evt-1"])
    with pytest.raises(UntrustedMemoryError):
        store.read_refs(["episodic:latest"])


@pytest.mark.parametrize("make_store", [jsonl_store, sqlite_store])
def test_append_refuses_to_extend_a_tampered_log(tmp_path, make_store):
    store = filled(tmp_path, make_store)
    tamper(tmp_path, store, 0, add_notes)
    with pytest.raises(UntrustedMemoryError):
        store.append(node_id="node-1", round=3, objective="Build: result", output=output("step-3"))
        store.events()


def test_untrusted_memory_error_is_a_value_error():
    assert issubclass(UntrustedMemoryError, ValueError)
