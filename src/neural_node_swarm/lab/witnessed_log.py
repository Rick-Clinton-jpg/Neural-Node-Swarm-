"""Witnessed log: give the anchor freshness.

The bounded store serves only what the caller's anchor covers, but it accepts
any anchor the on-disk log extends. A dropped tail handed forward together with
the older anchor that matches it (``anchor_stale_rollback``) is a consistent
pair, so it is served: an anchor alone carries no freshness.

The witnessed store keeps an ``AnchorWitness`` in its own file next to, and
outside, events.jsonl. The witness records the highest anchor ever issued. It
refuses to record a lower count (``witness_regress``) or a different head at
an equal count (``witness_fork``). The store, on open, on every read and
before every append, refuses (``anchor_stale``) a caller anchor whose count is
behind the witness, and (``anchor_witness_fork``) one that disagrees with the
witness head at the witness count. Each of its own appends advances the
witness. A caller anchor ahead of the witness, over a log that verifies to it,
is accepted and recorded.

What a witness cannot see, and the eight-target catalog records as a miss
(``witness_rollback``): an attacker who rolls the witness file back together
with the log. Trust moved to the witness file; it did not vanish. Freshness
that survives that needs a witness the attacker cannot write.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from neural_node_swarm.lab.anchored_log import Anchor, AnchorRefused, check_anchor, seals_of
from neural_node_swarm.lab.bounded_log import BoundedMemoryStore
from neural_node_swarm.lab.sealed_log import GENESIS


def witness_path_for(path: str | Path) -> Path:
    """Default witness file for an event log: a sibling, never the log itself."""
    path = Path(path)
    return path.with_name(path.stem + ".witness.json")


class AnchorWitness:
    """Monotonic record of the highest anchor ever issued for one log."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def current(self) -> Anchor:
        if not self.path.exists():
            return Anchor(0, GENESIS)
        try:
            record = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(record, dict) or set(record) != {"count", "head"}:
                raise ValueError("shape")
            return check_anchor((record["count"], record["head"]))
        except ValueError as error:
            raise AnchorRefused("witness_malformed", str(self.path.name)) from error

    def record(self, anchor: Any) -> Anchor:
        anchor = check_anchor(anchor)
        current = self.current()
        if anchor.count < current.count:
            raise AnchorRefused("witness_regress", f"{anchor.count} < {current.count}")
        if anchor.count == current.count:
            if anchor.head != current.head:
                raise AnchorRefused("witness_fork", f"at {anchor.count}")
            return current
        self.path.parent.mkdir(parents=True, exist_ok=True)
        scratch = self.path.with_name(self.path.name + ".tmp")
        scratch.write_text(json.dumps({"count": anchor.count, "head": anchor.head}, sort_keys=True) + "\n",
                           encoding="utf-8")
        os.replace(scratch, self.path)
        return anchor


class WitnessedMemoryStore(BoundedMemoryStore):
    """Bounded store that refuses any anchor behind the witness it keeps."""

    def __init__(self, path: str | Path, anchor: Any = None, witness_path: str | Path | None = None):
        self.witness = AnchorWitness(witness_path if witness_path is not None else witness_path_for(path))
        super().__init__(path, anchor=anchor)
        self._checked()
        if self.anchor.count > self.witness.current().count:
            self.witness.record(self.anchor)

    def _checked(self) -> list[dict[str, Any]]:
        events = super()._checked()
        witnessed = self.witness.current()
        count, head = self.anchor
        if count < witnessed.count:
            raise AnchorRefused("anchor_stale", f"{count} < {witnessed.count}")
        if seals_of(events[:witnessed.count])[-1] != witnessed.head:
            raise AnchorRefused("anchor_witness_fork", f"at {witnessed.count}")
        if count == witnessed.count and head != witnessed.head:
            raise AnchorRefused("anchor_witness_fork", f"at {count}")
        return events

    def append(self, *, node_id: str, round: int, objective: str, output: Any) -> dict[str, Any]:
        event = super().append(node_id=node_id, round=round, objective=objective, output=output)
        self.witness.record(self.anchor)
        return event
