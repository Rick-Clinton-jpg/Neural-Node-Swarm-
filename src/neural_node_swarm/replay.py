from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

from .memory import MemoryStore
from .orchestrator import Orchestrator


class _ListStorage:
    """In-memory append-only storage used only to regenerate a replayed stream."""

    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []

    def append_event(self, event: dict[str, Any]) -> None:
        if any(existing["event_id"] == event["event_id"] for existing in self._events):
            raise ValueError(f"duplicate event_id: {event['event_id']}")
        self._events.append(json.loads(json.dumps(event, sort_keys=True)))

    def list_events(self) -> list[dict[str, Any]]:
        return [dict(event) for event in self._events]

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        return next((event for event in self._events if event["event_id"] == event_id), None)


def _serialize(event: dict[str, Any]) -> str:
    return json.dumps(event, sort_keys=True)


def replay_relay(
    log: list[dict[str, Any]],
    node_factory: Callable[[dict[str, Any], int], dict[str, Any]],
    *,
    id_source: Callable[[], str],
    clock: Callable[[], datetime],
) -> dict[str, Any]:
    """Re-run a single-node relay from its episodic log and compare byte-for-byte.

    The initial objective and node id are taken from the first logged event. The
    relay is re-run with fresh, caller-supplied ``id_source`` and ``clock`` into an
    in-memory store, and each regenerated event is serialized the same way the
    JSONL backend writes it. ``mismatch_index`` is the first position where the
    streams differ (including a length difference), or ``None`` when identical.
    """
    recorded = [_serialize(event) for event in log]
    if not log:
        return {"replays": False, "mismatch_index": 0, "recorded": recorded, "replayed": []}
    objective, node_id = log[0].get("objective"), log[0].get("node_id")
    if not isinstance(objective, str) or not isinstance(node_id, str):
        return {"replays": False, "mismatch_index": 0, "recorded": recorded, "replayed": []}
    store = MemoryStore(storage=_ListStorage(), id_source=id_source, clock=clock)
    try:
        Orchestrator(store, node_factory).run(objective, node_id=node_id)
    except ValueError:
        pass
    replayed = [_serialize(event) for event in store.events()]
    mismatch_index = None
    for index in range(max(len(recorded), len(replayed))):
        if index >= len(recorded) or index >= len(replayed) or recorded[index] != replayed[index]:
            mismatch_index = index
            break
    return {"replays": mismatch_index is None, "mismatch_index": mismatch_index, "recorded": recorded, "replayed": replayed}
