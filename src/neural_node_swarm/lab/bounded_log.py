"""Bounded log: serve only what the anchor covers.

The anchored store refuses a log that does not extend the caller's anchor, but
it accepts and serves any log that does. A forged event appended past the
anchor with a correctly recomputed seal (SHA-256 is unkeyed) extends the chain,
so it is served by ``events()`` and becomes ``episodic:latest``.

The bounded store reads through a view of the sealed storage that, after the
anchored checks, returns only the first ``anchor.count`` verified events. So
``events``, ``read_refs``, ``episodic:latest``, the hardened replay check and
the ref-binding check all see the anchored prefix and nothing past it. Before
writing, the view refuses (``anchor_unanchored_tail``) a log that holds lines
the anchor does not cover, so a store never extends lines it cannot vouch for.
Its own appends advance the anchor and become readable.

What a bound cannot see, and stays a recorded miss: a rollback presented with
an older anchor the caller still accepts. A store opened without an anchor
adopts the current tip, as the anchored store does.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from neural_node_swarm.lab.anchored_log import AnchoredMemoryStore, AnchorRefused
from neural_node_swarm.lab.sealed_log import SealedJsonlStorage


class BoundedStorageView:
    """Storage adapter exposing only the anchored prefix of a sealed log."""

    def __init__(self, store: "BoundedMemoryStore", raw: SealedJsonlStorage):
        self.store = store
        self.raw = raw

    def list_events(self) -> list[dict[str, Any]]:
        events = self.store._checked()  # chain, rollback and head checks on the full log
        return events[:self.store.anchor.count]

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        return next((event for event in self.list_events() if event["event_id"] == event_id), None)

    def append_event(self, event: dict[str, Any]) -> None:
        events = self.store._checked()
        if len(events) != self.store.anchor.count:
            raise AnchorRefused("anchor_unanchored_tail", f"{len(events)} > {self.store.anchor.count}")
        self.raw.append_event(event)

    def head(self) -> str:
        return self.store.anchor.head


class BoundedMemoryStore(AnchoredMemoryStore):
    """Anchored store whose reads and writes stop at the anchor it holds."""

    def __init__(self, path: str | Path, anchor: Any = None):
        super().__init__(path, anchor=anchor)
        self.view = BoundedStorageView(self, self.storage)
        self.inner.storage = self.view

    def append(self, *, node_id: str, round: int, objective: str, output: Any) -> dict[str, Any]:
        before = self._checked()
        if len(before) != self.anchor.count:
            raise AnchorRefused("anchor_unanchored_tail", f"{len(before)} > {self.anchor.count}")
        return super().append(node_id=node_id, round=round, objective=objective, output=output)
