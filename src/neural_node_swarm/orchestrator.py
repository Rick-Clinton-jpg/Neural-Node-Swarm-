from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .memory import MemoryStore


class Orchestrator:
    """Sequential three-round relay; successor receives only the validated objective."""

    def __init__(self, memory: MemoryStore, node_factory: Callable[[dict[str, Any], int], dict[str, Any]]):
        self.memory = memory
        self.node_factory = node_factory

    def run(self, objective: str, *, node_id: str = "node-1") -> dict[str, Any]:
        current = {"schema_version": "1.0", "step_id": "step-0", "objective": objective, "success_criteria": ["next objective is schema-valid"], "required_memory_refs": []}
        for round_number in range(1, 4):
            output = self.node_factory(current, round_number)
            self.memory.append(node_id=node_id, round=round_number, objective=current["objective"], output=output)
            current = output
        return current

    def run_chain(self, objective: str, *, node_factory: Callable[[str], Callable[[dict[str, Any], int], dict[str, Any]]], node_count: int = 2) -> dict[str, Any]:
        """Run non-overlapping nodes. A new node is created only after its predecessor's TTL."""
        if node_count < 1:
            raise ValueError("node_count must be positive")
        current = {"schema_version": "1.0", "step_id": "step-0", "objective": objective, "success_criteria": ["next objective is schema-valid"], "required_memory_refs": []}
        for index in range(1, node_count + 1):
            node_id = f"node-{index}"
            node = node_factory(node_id)
            for round_number in range(1, 4):
                output = node(current, round_number)
                self.memory.append(node_id=node_id, round=round_number, objective=current["objective"], output=output)
                current = output
        return current
