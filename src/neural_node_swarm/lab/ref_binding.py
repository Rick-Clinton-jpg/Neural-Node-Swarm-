"""Ref-binding policy layered on the channel budget.

The budget bounds two channels it cannot close: required_memory_refs (16
distinct refs of up to 256 chars each, 4096 bytes, never resolved at commit) and
confidence (any IEEE double in [0, 1], about 62 bits of mantissa payload). This
policy closes both at commit time:

- every required_memory_ref must resolve through the store's own ``read_refs``
  against events already committed, so unsupported, dangling or unresolvable
  refs are refused when written, not only when a successor reads them. What is
  left is a choice among committed events, not free bytes;
- confidence must be absent or carry at most two decimal places, which leaves
  101 values (under 7 bits).

Order: canonicalize once, channel budget, verifier, confidence, refs, then the
budgeted (and so hardened) append of the same canonical plain dict.
"""
from __future__ import annotations

from typing import Any

from neural_node_swarm.lab.channel_budget import BudgetedMemoryStore, BudgetRejected, check_budget
from neural_node_swarm.lab.hardened_store import HardenedCommitRejected, canonicalize
from neural_node_swarm.memory import MemoryStore
from neural_node_swarm.verifier import verify_node_output

CONFIDENCE_DECIMALS = 2


class RefBindingRejected(HardenedCommitRejected):
    """An output was refused by the ref-binding policy."""


def confidence_is_coarse(value: Any) -> bool:
    """True if ``value`` is an int or float in [0, 1] with at most two decimal places."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if not 0 <= value <= 1:
        return False
    return round(value, CONFIDENCE_DECIMALS) == value


class RefBoundMemoryStore:
    """Channel budget plus commit-time ref resolution and coarse confidence."""

    def __init__(self, inner: MemoryStore):
        self.inner = inner
        self.budgeted = BudgetedMemoryStore(inner)

    def append(self, *, node_id: str, round: int, objective: str, output: Any) -> dict[str, Any]:
        try:
            canonical = canonicalize(output)
        except UnicodeError as error:  # lone surrogates cannot be encoded as UTF-8
            raise BudgetRejected("not_canonical_json", type(error).__name__) from error
        check_budget(canonical)
        result = verify_node_output(canonical)
        if result["status"] != "passed":
            failed = [check["name"] for check in result["checks"] if not check["passed"]]
            raise RefBindingRejected("verifier_rejected", ",".join(failed))
        if "confidence" in canonical and not confidence_is_coarse(canonical["confidence"]):
            raise RefBindingRejected("confidence_precision", repr(canonical["confidence"]))
        refs = canonical["required_memory_refs"]
        for ref in refs:
            try:
                self.inner.read_refs([ref])
            except KeyError as error:
                raise RefBindingRejected("memory_ref_unbound", ref[:64]) from error
        return self.budgeted.append(node_id=node_id, round=round, objective=objective, output=canonical)

    def events(self) -> list[dict[str, Any]]:
        return self.inner.events()

    def read_refs(self, refs: list[str]) -> list[dict[str, Any]]:
        return self.budgeted.read_refs(refs)

    def distilled(self) -> dict[str, Any]:
        return self.inner.distilled()
