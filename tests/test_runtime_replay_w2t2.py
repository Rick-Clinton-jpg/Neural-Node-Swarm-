import json
from datetime import datetime, timedelta, timezone

import pytest

from neural_node_swarm import MemoryStore, Orchestrator
from neural_node_swarm.node import DisposableNode
from neural_node_swarm.replay import counter_id_source, replay_run, stepping_clock

START = datetime(2026, 9, 5, 12, 0, 0, tzinfo=timezone.utc)
STEP = timedelta(seconds=1)


def node_factory(current, round_number):
    return DisposableNode("node-1").fire(current, round_number)


def record_run(tmp_path):
    log = tmp_path / "run" / "events.jsonl"
    store = MemoryStore(log, id_source=counter_id_source(), clock=stepping_clock(START, STEP))
    Orchestrator(store, node_factory).run("Build: replayable relay")
    return log


def replay(log):
    return replay_run(log, node_factory, id_source=counter_id_source(), clock=stepping_clock(START, STEP))


def test_helpers_are_fresh_and_deterministic():
    first, second = counter_id_source(), counter_id_source()
    assert [first(), first()] == ["evt-000001", "evt-000002"]
    assert second() == "evt-000001"
    assert counter_id_source("x")() == "x-000001"
    clock = stepping_clock(START, STEP)
    assert [clock(), clock()] == [START, START + STEP]
    assert stepping_clock(START, STEP)() == START


def test_untouched_log_replays_byte_identically(tmp_path):
    log = record_run(tmp_path)
    original = log.read_bytes()
    result = replay(log)
    assert result["status"] == "replayed"
    assert result["first_divergence"] is None
    assert result["recorded_events"] == 3
    assert result["replayed_events"] == 3
    assert result["replayed_bytes"] == original
    assert log.read_bytes() == original


def _rewrite(log, lines):
    log.write_bytes(b"".join(lines))


def _edit_line(log, index, mutate):
    lines = log.read_bytes().splitlines(keepends=True)
    record = json.loads(lines[index])
    mutate(record)
    lines[index] = (json.dumps(record, sort_keys=True) + "\n").encode("utf-8")
    _rewrite(log, lines)


def test_changed_output_field_diverges(tmp_path):
    log = record_run(tmp_path)
    _edit_line(log, 1, lambda r: r["output"].__setitem__("objective", "Advance: forged"))
    before = log.read_bytes()
    result = replay(log)
    assert result["status"] == "diverged"
    assert result["first_divergence"] == 1
    assert log.read_bytes() == before


def test_changed_recorded_at_diverges(tmp_path):
    log = record_run(tmp_path)
    _edit_line(log, 2, lambda r: r.__setitem__("recorded_at", "2030-01-01T00:00:00+00:00"))
    result = replay(log)
    assert result["status"] == "diverged"
    assert result["first_divergence"] == 2


def test_removed_line_diverges(tmp_path):
    log = record_run(tmp_path)
    lines = log.read_bytes().splitlines(keepends=True)
    _rewrite(log, lines[:1] + lines[2:])
    result = replay(log)
    assert result["status"] == "diverged"
    assert result["first_divergence"] == 1
    assert result["recorded_events"] == 2
    assert result["replayed_events"] == 3


def test_removed_last_line_diverges(tmp_path):
    log = record_run(tmp_path)
    lines = log.read_bytes().splitlines(keepends=True)
    _rewrite(log, lines[:-1])
    result = replay(log)
    assert result["status"] == "diverged"
    assert result["first_divergence"] == 2


def test_appended_line_diverges(tmp_path):
    log = record_run(tmp_path)
    lines = log.read_bytes().splitlines(keepends=True)
    _rewrite(log, lines + [lines[-1]])
    result = replay(log)
    assert result["status"] == "diverged"
    assert result["first_divergence"] == 3
    assert result["recorded_events"] == 4
    assert result["replayed_events"] == 3


def test_reused_id_source_or_clock_does_not_replay(tmp_path):
    log = record_run(tmp_path)
    ids = counter_id_source()
    ids()
    result = replay_run(log, node_factory, id_source=ids, clock=stepping_clock(START, STEP))
    assert result["status"] == "diverged"
    assert result["first_divergence"] == 0


def test_empty_or_garbled_log_diverges(tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_bytes(b"")
    assert replay(empty)["status"] == "diverged"
    garbled = tmp_path / "garbled.jsonl"
    garbled.write_bytes(b"not json\n")
    result = replay(garbled)
    assert result["status"] == "diverged"
    assert result["first_divergence"] == 0


def test_replay_does_not_use_memory_store_events(tmp_path, monkeypatch):
    log = record_run(tmp_path)

    def forbidden(self):
        if self.path is not None and self.path.resolve() == log.resolve():
            raise AssertionError("original log must be read as raw bytes")
        return original_events(self)

    original_events = MemoryStore.events
    monkeypatch.setattr(MemoryStore, "events", forbidden)
    assert replay(log)["status"] == "replayed"
