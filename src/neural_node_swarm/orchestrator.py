from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import Any

from .memory import MemoryStore
from .metrics import Metrics


class Orchestrator:
    """Sequential three-round relay; successor receives only the validated objective."""

    def __init__(self, memory: MemoryStore, node_factory: Callable[[dict[str, Any], int], dict[str, Any]], metrics: Metrics | None = None):
        self.memory = memory
        self.node_factory = node_factory
        self.metrics = metrics or Metrics()

    def run(self, objective: str, *, node_id: str = "node-1") -> dict[str, Any]:
        current = {"schema_version": "1.0", "step_id": "step-0", "objective": objective, "success_criteria": ["next objective is schema-valid"], "required_memory_refs": []}
        for round_number in range(1, 4):
            output = self.node_factory(current, round_number)
            self.memory.append(node_id=node_id, round=round_number, objective=current["objective"], output=output)
            self.metrics.rounds_completed += 1
            self.metrics.events_committed += 1
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

    async def run_async(self, objective: str, *, node_id: str = "node-1", timeout: float = 30.0, retries: int = 0) -> dict[str, Any]:
        """Async-ready relay with bounded node calls and single-threaded commits."""
        if timeout <= 0 or retries < 0:
            raise ValueError("timeout must be positive and retries cannot be negative")
        current = {"schema_version": "1.0", "step_id": "step-0", "objective": objective, "success_criteria": ["next objective is schema-valid"], "required_memory_refs": []}
        for round_number in range(1, 4):
            last_error = None
            for _attempt in range(retries + 1):
                try:
                    result = self.node_factory(current, round_number)
                    output = await asyncio.wait_for(result if inspect.isawaitable(result) else asyncio.to_thread(lambda: result), timeout)
                    self.memory.append(node_id=node_id, round=round_number, objective=current["objective"], output=output)
                    self.metrics.rounds_completed += 1
                    self.metrics.events_committed += 1
                    current = output
                    break
                except (asyncio.TimeoutError, ValueError) as error:
                    last_error = error
                    self.metrics.verification_failures += isinstance(error, ValueError)
                    if _attempt < retries:
                        self.metrics.retries += 1
            else:
                raise last_error
        return current
