"""Task-specific deterministic verifier for a narrow task with known ground truth.

The schema verifier only judges whether a node output is well-formed. This module
judges whether a relay achieved its objective on one concrete task: summing a fixed
list of integers. The final node output must be schema-valid and its objective must
be exactly ``ANSWER: <integer>`` in canonical form, equal to the ground truth.
Pure and offline: no clock, no randomness, no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from .verifier import verify_node_output

ANSWER_MARKER = "ANSWER:"
_ANSWER_RE = re.compile(r"ANSWER: (0|-?[1-9][0-9]*)")
TASK_VERIFIER_VERSION = "0.1.0"


@dataclass(frozen=True)
class SumTask:
    """Sum a fixed, non-empty tuple of integers."""

    numbers: tuple[int, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.numbers, tuple) or not self.numbers:
            raise ValueError("numbers must be a non-empty tuple")
        if not all(type(n) is int for n in self.numbers):
            raise ValueError("numbers must all be int (bool and float are not accepted)")

    def ground_truth(self) -> int:
        return sum(self.numbers)

    def prompt(self) -> str:
        listed = ", ".join(str(n) for n in self.numbers)
        return f"Sum the integers [{listed}] and finish with an objective of the form '{ANSWER_MARKER} <integer>'."


def parse_answer(objective: Any) -> int | None:
    """Return the integer answer if the objective is exactly 'ANSWER: <canonical int>'."""
    if not isinstance(objective, str):
        return None
    match = _ANSWER_RE.fullmatch(objective)
    return int(match.group(1)) if match else None


def verify_task_output(output: Any, task: SumTask) -> dict[str, Any]:
    """Judge a relay's final output against the task's ground truth."""
    expected = task.ground_truth()
    schema_result = verify_node_output(output)
    well_formed = schema_result["status"] == "passed"
    observed = parse_answer(output.get("objective")) if well_formed else None
    checks = [
        {"name": "well_formed", "passed": well_formed, "detail": "" if well_formed else "output failed verify_node_output"},
        {"name": "answer_present", "passed": observed is not None, "detail": "" if observed is not None else f"objective must be exactly '{ANSWER_MARKER} <integer>'"},
        {"name": "answer_correct", "passed": observed == expected, "detail": "" if observed == expected else "answer does not match ground truth"},
    ]
    status = "passed" if all(check["passed"] for check in checks) else "rejected"
    return {
        "step_id": schema_result.get("step_id", ""),
        "status": status,
        "expected": expected,
        "observed": observed,
        "checks": checks,
        "task_verifier_version": TASK_VERIFIER_VERSION,
    }
