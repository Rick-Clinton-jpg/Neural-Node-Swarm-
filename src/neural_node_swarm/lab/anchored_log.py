"""Anchored log: hold the head outside events.jsonl.

The sealed log re-verifies its SHA-256 chain on every read, but the chain lives
in the same file an attacker edits. A dropped tail is a valid prefix, and a
rewrite with every later seal recomputed is a valid chain (SHA-256 is unkeyed).
Both are served.

The anchored store gives the caller an ``Anchor(count, head)`` after every
append: the number of committed events and the seal of the last one. The caller
keeps it outside the log and passes it forward. A store opened with an anchor,
on every read and before every append, re-verifies the chain and refuses
(``AnchorRefused``) a log whose verified chain is shorter than ``count``
(``anchor_rollback``) or whose seal at ``count`` differs from ``head``
(``anchor_head_mismatch``). A longer log that extends the anchored prefix is
accepted, and the store's anchor advances only with its own appends.

What an anchor cannot see, and the six-target catalog records as a miss: a
rollback presented together with an older anchor the caller still accepts.
Freshness has to come from somewhere the attacker cannot rewind.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, NamedTuple

from neural_node_swarm.lab.sealed_log import GENESIS, SealBroken, SealedMemoryStore, seal_of

_HEX64 = re.compile(r"[0-9a-f]{64}")


class Anchor(NamedTuple):
    count: int
    head: str


class AnchorRefused(SealBroken):
    """The on-disk log does not extend the anchor the caller holds."""


def check_anchor(anchor: Any) -> Anchor:
    """Return ``anchor`` as an ``Anchor`` or raise ``AnchorRefused('anchor_malformed')``."""
    if (not isinstance(anchor, tuple) or len(anchor) != 2
            or isinstance(anchor[0], bool) or not isinstance(anchor[0], int) or anchor[0] < 0
            or not isinstance(anchor[1], str) or not _HEX64.fullmatch(anchor[1])):
        raise AnchorRefused("anchor_malformed", repr(anchor)[:64])
    return Anchor(anchor[0], anchor[1])


def seals_of(events: list[dict[str, Any]]) -> list[str]:
    """Chain seals of already verified ``events``, GENESIS first."""
    seals = [GENESIS]
    for event in events:
        seals.append(seal_of(seals[-1], event))
    return seals


class AnchoredMemoryStore(SealedMemoryStore):
    """Sealed store that refuses any log not extending the caller's anchor."""

    def __init__(self, path: str | Path, anchor: Any = None):
        super().__init__(path)
        self.anchor = check_anchor(anchor) if anchor is not None else None
        if self.anchor is None:
            self.anchor = Anchor(*self._tip(self.storage.list_events()))

    @staticmethod
    def _tip(events: list[dict[str, Any]]) -> tuple[int, str]:
        return len(events), seals_of(events)[-1]

    def _checked(self) -> list[dict[str, Any]]:
        events = self.storage.list_events()  # raises SealBroken on a broken chain
        count, head = self.anchor
        if len(events) < count:
            raise AnchorRefused("anchor_rollback", f"{len(events)} < {count}")
        if seals_of(events[:count])[-1] != head:
            raise AnchorRefused("anchor_head_mismatch", f"at {count}")
        return events

    def append_anchored(self, *, node_id: str, round: int, objective: str, output: Any) -> Anchor:
        self.append(node_id=node_id, round=round, objective=objective, output=output)
        return self.anchor

    def append(self, *, node_id: str, round: int, objective: str, output: Any) -> dict[str, Any]:
        before = self._checked()
        event = super().append(node_id=node_id, round=round, objective=objective, output=output)
        events = self._checked()
        if len(events) != len(before) + 1 or events[:len(before)] != before:
            raise AnchorRefused("anchor_append_raced", f"{len(before)} -> {len(events)}")
        self.anchor = Anchor(*self._tip(events))
        return event

    def events(self) -> list[dict[str, Any]]:
        self._checked()
        return super().events()

    def read_refs(self, refs: list[str]) -> list[dict[str, Any]]:
        self._checked()
        return super().read_refs(refs)
