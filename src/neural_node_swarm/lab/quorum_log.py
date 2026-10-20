"""Quorum log: make freshness survive one rewritten witness.

The witnessed store keeps the highest anchor ever issued in a single witness
file beside events.jsonl. Whoever rolls that one file back together with the
log (``witness_rollback``) rewinds freshness: the stale anchor matches the
witness, so the dropped tail goes unseen.

The quorum store keeps a ``QuorumWitness`` over N ``AnchorWitness`` replicas,
each in its own directory (the first is the witnessed store's default sibling
file). Every append advances every replica. The current anchor is the highest
any replica holds, after checking the replicas agree: two replicas at the same
count with different heads are refused (``witness_fork``), and so is any
replica whose head is not the seal of the verified chain at its count. A caller
anchor behind the current anchor is refused (``anchor_stale``), so rolling back
all but one replica (``quorum_partial_rollback``) is caught. A rolled-back or
missing replica is healed by the next honest append.

What a quorum cannot see, and the nine-target catalog records as a miss
(``quorum_full_rollback``): an attacker who rolls back every replica. Trust
moved to "at least one replica the attacker did not rewrite"; it did not vanish.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from neural_node_swarm.lab.anchored_log import Anchor, AnchorRefused, check_anchor, seals_of
from neural_node_swarm.lab.bounded_log import BoundedMemoryStore
from neural_node_swarm.lab.sealed_log import GENESIS
from neural_node_swarm.lab.witnessed_log import AnchorWitness, WitnessedMemoryStore, witness_path_for

DEFAULT_REPLICAS = 3


def replica_paths_for(path: str | Path, replicas: int = DEFAULT_REPLICAS) -> list[Path]:
    """Default replica files: the witnessed sibling, then one per sub-directory."""
    first = witness_path_for(path)
    return [first] + [first.parent / f"witness-replica-{index}" / first.name for index in range(1, replicas)]


class QuorumWitness:
    """Highest anchor held by any of N witness replicas that agree with each other."""

    def __init__(self, paths: Sequence[str | Path]):
        self.replicas = [AnchorWitness(path) for path in paths]
        if not self.replicas:
            raise ValueError("a quorum needs at least one replica")
        if len({replica.path.resolve().parent for replica in self.replicas}) != len(self.replicas):
            raise ValueError("witness replicas must live in separate directories")

    def anchors(self) -> list[Anchor]:
        return [replica.current() for replica in self.replicas]

    def current(self) -> Anchor:
        anchors = self.anchors()
        by_count: dict[int, str] = {}
        for anchor in anchors:
            if by_count.setdefault(anchor.count, anchor.head) != anchor.head:
                raise AnchorRefused("witness_fork", f"replicas disagree at {anchor.count}")
        return max(anchors, key=lambda anchor: anchor.count, default=Anchor(0, GENESIS))

    def record(self, anchor: Any) -> Anchor:
        anchor = check_anchor(anchor)
        current = self.current()
        if anchor.count < current.count:
            raise AnchorRefused("witness_regress", f"{anchor.count} < {current.count}")
        if anchor.count == current.count and anchor.head != current.head:
            raise AnchorRefused("witness_fork", f"at {anchor.count}")
        for replica in self.replicas:
            replica.record(anchor)
        return anchor


class QuorumMemoryStore(WitnessedMemoryStore):
    """Witnessed store whose freshness comes from the highest of N agreeing replicas."""

    def __init__(self, path: str | Path, anchor: Any = None,
                 witness_paths: Sequence[str | Path] | None = None):
        paths = list(witness_paths) if witness_paths is not None else replica_paths_for(path)
        if any(Path(replica).resolve() == Path(path).resolve() for replica in paths):
            raise ValueError("a witness replica cannot be the event log")
        self.witness = QuorumWitness(paths)  # type: ignore[assignment]
        BoundedMemoryStore.__init__(self, path, anchor=anchor)
        self._checked()
        if self.anchor.count > self.witness.current().count:
            self.witness.record(self.anchor)

    def _checked(self) -> list[dict[str, Any]]:
        events = super()._checked()  # anchor_stale if behind the highest replica
        seals = seals_of(events)
        for replica in self.witness.anchors():
            if replica.count >= len(seals) or seals[replica.count] != replica.head:
                raise AnchorRefused("witness_fork", f"replica off the chain at {replica.count}")
        return events
