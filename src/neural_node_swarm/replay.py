"""Deterministic replay of recorded relay runs.

A run recorded with a deterministic ID source and clock can be re-executed and
compared byte for byte against its episodic log. The original log is read as raw
bytes, never through MemoryStore.events(), and is never written to.
"""
from __future__ import annotations

import json
import tempfile
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .memory import MemoryStore
from .orchestrator import Orchestrator


def counter_id_source(prefix: str = "evt") -> Callable[[], str]:
    """Return a fresh callable yielding prefix-000001, prefix-000002, ..."""
    count = 0

    def next_id() -> str:
        nonlocal count
        count += 1
        return f"{prefix}-{count:06d}"

    return next_id


def stepping_clock(start: datetime, step: timedelta) -> Callable[[], datetime]:
    """Return a fresh clock yielding start, start + step, start + 2*step, ..."""
    ticks = 0

    def now() -> datetime:
        nonlocal ticks
        value = start + step * ticks
        ticks += 1
        return value

    return now


def _first_difference(recorded: list[bytes], replayed: list[bytes]) -> int | None:
    for index, (left, right) in enumerate(zip(recorded, replayed)):
        if left != right:
            return index
    if len(recorded) != len(replayed):
        return min(len(recorded), len(replayed))
    return None


def replay_run(
    log_path: str | Path,
    node_factory: Callable[[dict[str, Any], int], dict[str, Any]],
    *,
    id_source: Callable[[], str],
    clock: Callable[[], datetime],
    node_id: str = "node-1",
) -> dict[str, Any]:
    """Re-run a recorded relay and compare the new event stream with the log byte for byte.

    ``id_source`` and ``clock`` must be fresh instances equivalent to the ones the
    original run used. Returns a dict with ``status`` ("replayed" or "diverged"),
    ``recorded_events``, ``replayed_events``, ``first_divergence`` (index of the
    first differing line, or None) and ``replayed_bytes``.
    """
    recorded_bytes = Path(log_path).read_bytes()
    recorded_lines = recorded_bytes.splitlines(keepends=True)

    def diverged(index: int, replayed: bytes = b"", reason: str | None = None) -> dict[str, Any]:
        result = {
            "status": "diverged",
            "recorded_events": len(recorded_lines),
            "replayed_events": len(replayed.splitlines()),
            "first_divergence": index,
            "replayed_bytes": replayed,
        }
        if reason is not None:
            result["reason"] = reason
        return result

    if not recorded_lines:
        return diverged(0, reason="empty log")
    try:
        first = json.loads(recorded_lines[0])
        objective = first["objective"]
    except (ValueError, KeyError, TypeError):
        return diverged(0, reason="first record has no readable objective")
    if not isinstance(objective, str):
        return diverged(0, reason="first record has no readable objective")

    with tempfile.TemporaryDirectory(prefix="nn-replay-") as directory:
        replay_path = Path(directory) / "replay.jsonl"
        store = MemoryStore(replay_path, id_source=id_source, clock=clock)
        Orchestrator(store, node_factory).run(objective, node_id=node_id)
        replayed_bytes = replay_path.read_bytes() if replay_path.exists() else b""

    replayed_lines = replayed_bytes.splitlines(keepends=True)
    index = _first_difference(recorded_lines, replayed_lines)
    if index is None and recorded_bytes != replayed_bytes:
        index = 0
    if index is not None:
        return diverged(index, replayed_bytes)
    return {
        "status": "replayed",
        "recorded_events": len(recorded_lines),
        "replayed_events": len(replayed_lines),
        "first_divergence": None,
        "replayed_bytes": replayed_bytes,
    }
