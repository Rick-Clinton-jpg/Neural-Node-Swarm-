from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass
class Metrics:
    rounds_completed: int = 0
    events_committed: int = 0
    verification_failures: int = 0
    retries: int = 0

    def snapshot(self) -> dict[str, int]:
        return asdict(self)
