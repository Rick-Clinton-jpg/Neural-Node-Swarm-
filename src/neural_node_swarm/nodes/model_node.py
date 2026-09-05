from __future__ import annotations

from typing import Any

from ..node_base import ModelClient


class ModelNode:
    """Disposable adapter around an injected structured-output model client."""

    def __init__(self, node_id: str, client: ModelClient, memory_reader):
        self.node_id = node_id
        self.client = client
        self.memory_reader = memory_reader

    def fire(self, current: dict[str, Any], round_number: int) -> dict[str, Any]:
        memory = self.memory_reader(current.get("required_memory_refs", []))
        return self.client.complete(objective=current["objective"], memory=memory, round_number=round_number)
