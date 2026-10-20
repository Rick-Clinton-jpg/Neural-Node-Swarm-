"""A narrow task with known ground truth and a deterministic task verifier.

The task: a relay starts from ``"Sum: a,b,c"`` and must end on the exact
objective ``"Answer: <a+b+c>"``. Verification is pure: no model calls,
network, randomness or clock.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from .verifier import Check, verify_node_output

TASK_VERIFIER_VERSION = "0.1.0"
_SUM_PATTERN = re.compile(r"Sum: (-?\d+(?:,-?\d+)*)")


def sum_objective(numbers: Iterable[int]) -> str:
    values = list(numbers)
    if not values or not all(isinstance(n, int) and not isinstance(n, bool) for n in values):
        raise ValueError("numbers must be a non-empty sequence of integers")
    return "Sum: " + ",".join(str(n) for n in values)


def expected_answer(objective: str) -> str:
    if not isinstance(objective, str):
        raise ValueError("objective must be a string")
    match = _SUM_PATTERN.fullmatch(objective)
    if match is None:
        raise ValueError(f"not a sum task objective: {objective!r}")
    return f"Answer: {sum(int(part) for part in match.group(1).split(','))}"


def verify_task_output(start_objective: str, output: Any) -> dict[str, Any]:
    target = expected_answer(start_objective)
    node_result = verify_node_output(output)
    checks = [Check("node_output", node_result["status"] == "passed", "output is not a valid node output")]
    if checks[0].passed:
        actual = output["objective"]
        checks.append(Check("task_answer", actual == target, f"expected {target!r}, got {actual!r}"))
    else:
        checks.append(Check("task_answer", False, "not checked: output is malformed"))
    status = "passed" if all(check.passed for check in checks) else "rejected"
    step_id = output.get("step_id", "") if isinstance(output, dict) else ""
    return {
        "schema_version": "1.0",
        "step_id": step_id,
        "status": status,
        "expected": target,
        "checks": [check.__dict__ for check in checks],
        "verifier_version": TASK_VERIFIER_VERSION,
    }


def verify_relay(start_objective: str, final_output: Any) -> dict[str, Any]:
    """Judge the final output of an Orchestrator run against the task's ground truth."""
    return verify_task_output(start_objective, final_output)
