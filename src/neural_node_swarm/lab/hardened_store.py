"""Hardened commit path wrapping the unmodified MemoryStore.

Closes three misses the adversarial catalog recorded against the stock runtime:

- dict-subclass TOCTOU: the output is serialized to JSON exactly once and parsed
  back into plain JSON types. The verifier sees, and the store commits, that
  canonical form, so what was verified is what lands in the trusted log.
- step_id forgery: a committed step_id must equal ``f"{node_id}:{round}"``.
- step_id replay: a step_id already present in the store is refused.

Every rejection raises ``HardenedCommitRejected`` (a ``ValueError``) with a
machine-readable ``reason``, and nothing is committed.
"""
from __future__ import annotations

import json
from typing import Any

from neural_node_swarm.memory import MemoryStore
from neural_node_swarm.verifier import verify_node_output


class HardenedCommitRejected(ValueError):
    """An output was refused by the hardened commit path."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def canonical_bytes(output: Any) -> bytes:
    """Serialize once to canonical JSON. NaN, Infinity and non-JSON types are refused."""
    try:
        text = json.dumps(output, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise HardenedCommitRejected("not_canonical_json", type(error).__name__) from error
    return text.encode("utf-8")


def canonicalize(output: Any) -> Any:
    """Return the plain-JSON value that ``canonical_bytes`` encodes."""
    return json.loads(canonical_bytes(output).decode("utf-8"))


def bound_step_id(node_id: str, round: int) -> str:
    return f"{node_id}:{round}"


class HardenedMemoryStore:
    """Drop-in for MemoryStore.append that verifies and commits only canonical bytes."""

    def __init__(self, inner: MemoryStore):
        self.inner = inner

    def append(self, *, node_id: str, round: int, objective: str, output: Any) -> dict[str, Any]:
        raw = canonical_bytes(output)
        canonical = json.loads(raw.decode("utf-8"))
        result = verify_node_output(canonical)
        if result["status"] != "passed":
            failed = [check["name"] for check in result["checks"] if not check["passed"]]
            raise HardenedCommitRejected("verifier_rejected", ",".join(failed))
        expected = bound_step_id(node_id, round)
        if canonical["step_id"] != expected:
            raise HardenedCommitRejected("step_id_unbound", f"expected {expected}")
        if any(event.get("step_id") == expected for event in self.inner.events()):
            raise HardenedCommitRejected("step_id_replayed", expected)
        return self.inner.append(node_id=node_id, round=round, objective=objective, output=canonical)

    def events(self) -> list[dict[str, Any]]:
        return self.inner.events()

    def read_refs(self, refs: list[str]) -> list[dict[str, Any]]:
        return self.inner.read_refs(refs)

    def distilled(self) -> dict[str, Any]:
        return self.inner.distilled()
