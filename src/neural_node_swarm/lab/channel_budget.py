"""Channel-budget policy layered on the hardened commit path.

The hardened path binds step_id and commits canonical bytes, but the free-text
fields stay open side channels. This policy bounds them deterministically:

- no Unicode "other" characters (categories Cc, Cf, Cs, Co, Cn) in any string,
  which closes zero-width and bidi encodings and refuses lone surrogates;
- required_memory_refs: no duplicates, at most ``MAX_MEMORY_REFS``, also enforced
  on ``read_refs`` so a successor cannot be amplified;
- success_criteria and constraints: at most ``MAX_ITEMS`` items and
  ``LIST_MAX_BYTES`` UTF-8 bytes in total;
- objective: at most ``OBJECTIVE_MAX_BYTES`` UTF-8 bytes (the schema caps chars).

It does not judge meaning: a reasoning trace that fits the budget still passes.
The budget makes that residual channel a fixed, measured number of bytes.
Checks run on the canonical form, and the canonical plain dict is what is passed
on to the hardened path, so the object cannot change between check and commit.
"""
from __future__ import annotations

import unicodedata
from typing import Any

from neural_node_swarm.lab.hardened_store import HardenedCommitRejected, HardenedMemoryStore, canonicalize
from neural_node_swarm.memory import MemoryStore

MAX_MEMORY_REFS = 16
MAX_ITEMS = 8
LIST_MAX_BYTES = 1000
OBJECTIVE_MAX_BYTES = 1000
BUDGETED_LISTS = ("success_criteria", "constraints")


class BudgetRejected(HardenedCommitRejected):
    """An output or read was refused by the channel-budget policy."""


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for key, item in value.items() for s in _strings(key) + _strings(item)]
    if isinstance(value, list):
        return [s for item in value for s in _strings(item)]
    return []


def _utf8_len(text: str) -> int:
    return len(text.encode("utf-8"))


def check_refs(refs: list[str]) -> None:
    if len(refs) > MAX_MEMORY_REFS:
        raise BudgetRejected("memory_ref_count", f"{len(refs)} > {MAX_MEMORY_REFS}")
    if len(set(refs)) != len(refs):
        raise BudgetRejected("duplicate_memory_ref")


def check_budget(canonical: Any) -> None:
    """Raise BudgetRejected if a canonical (plain JSON) output exceeds the budget."""
    for text in _strings(canonical):
        for char in text:
            if unicodedata.category(char).startswith("C"):
                raise BudgetRejected("forbidden_character", f"U+{ord(char):04X}")
    if not isinstance(canonical, dict):
        return  # the verifier rejects non-objects
    refs = canonical.get("required_memory_refs")
    if isinstance(refs, list):
        check_refs(refs)
    objective = canonical.get("objective")
    if isinstance(objective, str) and _utf8_len(objective) > OBJECTIVE_MAX_BYTES:
        raise BudgetRejected("objective_bytes", f"{_utf8_len(objective)} > {OBJECTIVE_MAX_BYTES}")
    for field in BUDGETED_LISTS:
        items = canonical.get(field)
        if not isinstance(items, list):
            continue
        if len(items) > MAX_ITEMS:
            raise BudgetRejected(f"{field}_count", f"{len(items)} > {MAX_ITEMS}")
        total = sum(_utf8_len(item) for item in items if isinstance(item, str))
        if total > LIST_MAX_BYTES:
            raise BudgetRejected(f"{field}_bytes", f"{total} > {LIST_MAX_BYTES}")


class BudgetedMemoryStore:
    """Hardened commit path plus the channel budget."""

    def __init__(self, inner: MemoryStore):
        self.inner = inner
        self.hardened = HardenedMemoryStore(inner)

    def append(self, *, node_id: str, round: int, objective: str, output: Any) -> dict[str, Any]:
        try:
            canonical = canonicalize(output)
        except UnicodeError as error:  # lone surrogates cannot be encoded as UTF-8
            raise BudgetRejected("not_canonical_json", type(error).__name__) from error
        check_budget(canonical)
        return self.hardened.append(node_id=node_id, round=round, objective=objective, output=canonical)

    def events(self) -> list[dict[str, Any]]:
        return self.inner.events()

    def read_refs(self, refs: list[str]) -> list[dict[str, Any]]:
        check_refs(list(refs))
        return self.inner.read_refs(refs)

    def distilled(self) -> dict[str, Any]:
        return self.inner.distilled()
