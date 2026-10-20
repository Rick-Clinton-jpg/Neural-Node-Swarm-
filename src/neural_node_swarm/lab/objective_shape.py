"""Shaped store: bound the shape of the one free-text field a successor inherits.

Every earlier target serves ``objective_reasoning_smuggle``: a reasoning trace
packed into ``objective`` up to the verifier's 1000-char cap. The objective has
to stay free text, so this store does not try to read intent. It bounds shape:
after the output is canonicalized once and the verifier accepts it, an
objective is refused (``objective_shape``) if it has

- more than ``MAX_CHARS`` (400) characters,
- more than ``MAX_SENTENCES`` (3) sentences, or
- a zlib-compressed size under half its UTF-8 size (repeated trace text).

Every measured value travels on the rejection as evidence. Outputs the verifier
or the canonicalizer refuses are passed through unchanged, so their earlier
rejection reasons stand. Everything else is the quorum store.

What shape cannot see, and the ten-target catalog records as a miss
(``objective_paraphrased_trace``): a short, non-repeating trace inside all three
limits. The channel is narrowed to under 400 characters, not closed.
"""
from __future__ import annotations

import re
import zlib
from typing import Any

from neural_node_swarm.lab.hardened_store import HardenedCommitRejected, canonicalize
from neural_node_swarm.lab.quorum_log import QuorumMemoryStore
from neural_node_swarm.verifier import verify_node_output

MAX_CHARS = 400
MAX_SENTENCES = 3
MIN_COMPRESSION_RATIO = 0.5
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")


class ObjectiveShapeRejected(HardenedCommitRejected):
    """An objective was refused for its shape; ``measured`` holds the evidence."""

    def __init__(self, measured: dict[str, int], violations: list[str]):
        super().__init__("objective_shape", ",".join(violations))
        self.measured = measured
        self.violations = violations


def measure_objective(text: str) -> dict[str, int]:
    encoded = text.encode("utf-8")
    return {"chars": len(text),
            "sentences": len([part for part in _SENTENCE_BREAK.split(text.strip()) if part]),
            "utf8_bytes": len(encoded),
            "compressed_bytes": len(zlib.compress(encoded, 9))}


def shape_violations(measured: dict[str, int]) -> list[str]:
    violations = []
    if measured["chars"] > MAX_CHARS:
        violations.append("chars")
    if measured["sentences"] > MAX_SENTENCES:
        violations.append("sentences")
    if measured["compressed_bytes"] < MIN_COMPRESSION_RATIO * measured["utf8_bytes"]:
        violations.append("compression")
    return violations


def check_objective_shape(output: Any) -> None:
    """Raise ObjectiveShapeRejected for a verifier-accepted output with a misshapen objective."""
    try:
        canonical = canonicalize(output)
    except (ValueError, UnicodeError):
        return  # the inherited commit path refuses it with its own reason
    if verify_node_output(canonical)["status"] != "passed":
        return
    measured = measure_objective(canonical["objective"])
    violations = shape_violations(measured)
    if violations:
        raise ObjectiveShapeRejected(measured, violations)


class ShapedMemoryStore(QuorumMemoryStore):
    """Quorum store that refuses objectives too long, too many sentences, or too repetitive."""

    def append(self, *, node_id: str, round: int, objective: str, output: Any) -> dict[str, Any]:
        check_objective_shape(output)
        return super().append(node_id=node_id, round=round, objective=objective, output=output)
