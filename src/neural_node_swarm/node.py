from __future__ import annotations

from typing import Any


class DisposableNode:
    """Reference node adapter. No state is retained between calls."""

    def __init__(self, node_id: str, *, fail_round: int | None = None):
        self.node_id = node_id
        self.fail_round = fail_round

    def fire(self, current: dict[str, Any], round_number: int) -> dict[str, Any]:
        if round_number == self.fail_round:
            return {"schema_version": "1.0", "step_id": f"{self.node_id}:{round_number}", "objective": "invalid output", "success_criteria": [], "required_memory_refs": [], "notes": "injected failure"}
        return {"schema_version": "1.0", "step_id": f"{self.node_id}:{round_number}", "objective": f"Advance: {current['objective']}", "success_criteria": ["next objective is schema-valid"], "required_memory_refs": ["episodic:latest"]}
