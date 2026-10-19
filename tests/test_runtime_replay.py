import json
from datetime import datetime, timezone

import pytest

from neural_node_swarm import MemoryStore, Orchestrator
from neural_node_swarm.node import DisposableNode
from neural_node_swarm.replay import replay_relay
from neural_node_swarm.sqlite_storage import SQLiteStorage


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


BACKENDS = [jsonl_store, sqlite_store]


def recorded_run(tmp_path, make_store):
    store = make_store(tmp_path)
    Orchestrator(store, DisposableNode("node-1").fire).run("Build: result", node_id="node-1")
    return store.events()


@pytest.mark.parametrize("make_store", BACKENDS)
def test_relay_with_injected_ids_and_clock_replays_byte_identical(tmp_path, make_store):
    log = recorded_run(tmp_path, make_store)
    assert len(log) == 3
    result = replay_relay(log, DisposableNode("node-1").fire, id_source=counter_ids(), clock=fixed_clock)
    assert result["replays"] is True
    assert result["mismatch_index"] is None
    assert result["replayed"] == [json.dumps(event, sort_keys=True) for event in log]


@pytest.mark.parametrize("make_store", BACKENDS)
def test_log_edited_after_run_does_not_replay(tmp_path, make_store):
    log = recorded_run(tmp_path, make_store)
    log[1]["output"]["objective"] = "Advance: something else"
    result = replay_relay(log, DisposableNode("node-1").fire, id_source=counter_ids(), clock=fixed_clock)
    assert result["replays"] is False
    assert result["mismatch_index"] == 1


def test_jsonl_file_edited_on_disk_does_not_replay(tmp_path):
    recorded_run(tmp_path, jsonl_store)
    path = tmp_path / "events.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[2] = lines[2].replace("2026-09-05T12:00:00+00:00", "2026-09-05T12:00:01+00:00")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log = MemoryStore(path).events()
    result = replay_relay(log, DisposableNode("node-1").fire, id_source=counter_ids(), clock=fixed_clock)
    assert result["replays"] is False
    assert result["mismatch_index"] == 2


def test_truncated_or_extended_log_does_not_replay(tmp_path):
    log = recorded_run(tmp_path, jsonl_store)
    truncated = replay_relay(log[:2], DisposableNode("node-1").fire, id_source=counter_ids(), clock=fixed_clock)
    assert truncated["replays"] is False
    assert truncated["mismatch_index"] == 2
    extended = replay_relay(log + [dict(log[-1])], DisposableNode("node-1").fire, id_source=counter_ids(), clock=fixed_clock)
    assert extended["replays"] is False
    assert extended["mismatch_index"] == 3


def test_empty_log_does_not_replay():
    result = replay_relay([], DisposableNode("node-1").fire, id_source=counter_ids(), clock=fixed_clock)
    assert result["replays"] is False
    assert result["mismatch_index"] == 0
