from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable


def consolidate(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Derive statistics only; no model or free-form summarization is involved."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    materialized = list(events)
    for event in materialized:
        key = event["objective"].split(":", 1)[0].strip().lower() or "unknown"
        grouped[key].append(event)
    patterns = []
    for key in sorted(grouped):
        group = grouped[key]
        passed = sum(event["verifier_result"]["status"] == "passed" for event in group)
        patterns.append({"key": key, "sample_size": len(group), "pass_rate": passed / len(group), "failure_count": len(group) - passed})
    return {"schema_version": "1.0", "memory_version": len(materialized), "source_event_ids": [event["event_id"] for event in materialized], "patterns": patterns, "updated_at": datetime.now(timezone.utc).isoformat()}
