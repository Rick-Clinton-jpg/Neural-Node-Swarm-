"""Sealed event log layered under the ref-binding policy.

Every earlier target verifies an output once, at commit, and then trusts
events.jsonl on disk. A line rewritten, inserted or reordered afterwards reaches
``read_refs`` and successors unverified. The sealed log closes that gap for
anyone who edits the log without recomputing the chain:

- each committed event is written with ``seal = SHA-256(prev_seal || canonical
  bytes of the event)``, starting from ``GENESIS``;
- every read (``events``, ``read_refs``, and the replay check inside append)
  re-verifies the whole chain and raises ``SealBroken`` on the first bad line,
  so a broken log is neither served nor extended.

What a chain alone cannot see, and the five-target catalog records as misses:
a dropped tail (the shorter log is a valid prefix) and an attacker who rewrites
a line and recomputes every later seal (SHA-256 is unkeyed). Both need a head
held outside the log.

The storage adapter plugs into the unmodified ``MemoryStore`` through its
``storage=`` parameter, so the verifier and event shape are the stock ones.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from neural_node_swarm.lab.hardened_store import HardenedCommitRejected, canonical_bytes
from neural_node_swarm.lab.ref_binding import RefBoundMemoryStore
from neural_node_swarm.memory import MemoryStore

GENESIS = "0" * 64
SEAL_FIELD = "seal"


class SealBroken(HardenedCommitRejected):
    """The on-disk log does not verify against its seal chain."""


def seal_of(previous: str, event: dict[str, Any]) -> str:
    """Chain hash of ``event`` (without its seal) after ``previous``."""
    return hashlib.sha256(bytes.fromhex(previous) + canonical_bytes(event)).hexdigest()


class SealedJsonlStorage:
    """JSONL storage adapter whose lines carry a SHA-256 seal chain."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _verified(self) -> tuple[list[dict[str, Any]], str]:
        if not self.path.exists():
            return [], GENESIS
        events: list[dict[str, Any]] = []
        previous = GENESIS
        for number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError as error:
                raise SealBroken("log_seal_unparseable", f"line {number}") from error
            if not isinstance(record, dict) or not isinstance(record.get(SEAL_FIELD), str):
                raise SealBroken("log_seal_missing", f"line {number}")
            event = {key: value for key, value in record.items() if key != SEAL_FIELD}
            try:
                expected = seal_of(previous, event)
            except HardenedCommitRejected as error:
                raise SealBroken("log_seal_unparseable", f"line {number}") from error
            if record[SEAL_FIELD] != expected:
                raise SealBroken("log_seal_mismatch", f"line {number}")
            events.append(event)
            previous = expected
        return events, previous

    def append_event(self, event: dict[str, Any]) -> None:
        _, previous = self._verified()
        record = dict(event)
        record[SEAL_FIELD] = seal_of(previous, event)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")

    def list_events(self) -> list[dict[str, Any]]:
        return self._verified()[0]

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        return next((event for event in self.list_events() if event["event_id"] == event_id), None)

    def head(self) -> str:
        return self._verified()[1]


class SealedMemoryStore(RefBoundMemoryStore):
    """Ref-binding commit policy over a sealed, re-verified event log."""

    def __init__(self, path: str | Path):
        self.storage = SealedJsonlStorage(path)
        self.path = self.storage.path
        super().__init__(MemoryStore(storage=self.storage))
