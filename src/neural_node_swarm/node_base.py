from __future__ import annotations

from typing import Any, Protocol


class Node(Protocol):
    node_id: str

    def fire(self, current: dict[str, Any], round_number: int) -> dict[str, Any]: ...


class ModelClient(Protocol):
    def complete(self, *, objective: str, memory: list[dict[str, Any]], round_number: int) -> dict[str, Any]: ...
