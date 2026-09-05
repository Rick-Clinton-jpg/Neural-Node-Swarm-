from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .verifier import verify_node_output


class MemoryStore:
    """Small append-only episodic store with mechanically derived summaries."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def append(self, *, node_id: str, round: int, objective: str, output: Any) -> dict[str, Any]:
        result = verify_node_output(output)
        if result["status"] != "passed":
            raise ValueError("unverified output cannot be committed")
        event = {"schema_version": "1.0", "event_id": f"evt-{datetime.now(timezone.utc).timestamp()}", "step_id": output["step_id"], "node_id": node_id, "round": round, "objective": objective, "output": output, "verifier_result": result, "recorded_at": datetime.now(timezone.utc).isoformat()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, sort_keys=True) + "\n")
        return event

    def events(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line]

    def read_refs(self, refs: list[str]) -> list[dict[str, Any]]:
        """Resolve only explicit, read-only memory references."""
        events = self.events()
        by_id = {event["event_id"]: event for event in events}
        resolved: list[dict[str, Any]] = []
        for ref in refs:
            if ref == "episodic:latest":
                if events:
                    resolved.append(events[-1])
            elif ref.startswith("episodic:event:"):
                event = by_id.get(ref.removeprefix("episodic:event:"))
                if event is None:
                    raise KeyError(f"unknown memory reference: {ref}")
                resolved.append(event)
            else:
                raise KeyError(f"unsupported memory reference: {ref}")
        return resolved

    def distilled(self) -> dict[str, Any]:
        events = self.events()
        passed = sum(event["verifier_result"]["status"] == "passed" for event in events)
        return {"schema_version": "1.0", "memory_version": len(events), "source_event_ids": [event["event_id"] for event in events], "patterns": [{"key": "all", "sample_size": len(events), "pass_rate": passed / len(events) if events else 0.0, "failure_count": len(events) - passed}] if events else [], "updated_at": datetime.now(timezone.utc).isoformat()}

    def should_consolidate(self, *, interval: int = 10) -> bool:
        """Deterministic backstop trigger; failure-pattern triggers can be added later."""
        if interval < 1:
            raise ValueError("interval must be positive")
        return bool(self.events()) and len(self.events()) % interval == 0
