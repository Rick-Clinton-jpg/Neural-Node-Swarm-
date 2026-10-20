from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any, Callable

from .verifier import VerificationError, verify_node_output
from .consolidation import consolidate

_SCHEMA_FILES = ("memory_event.schema.json", "node_output.schema.json", "verifier_result.schema.json")


@lru_cache(maxsize=1)
def _memory_event_validator():
    import jsonschema
    from referencing import Registry, Resource

    directory = resources.files("neural_node_swarm").joinpath("schemas")
    schemas = {name: json.loads(directory.joinpath(name).read_text(encoding="utf-8")) for name in _SCHEMA_FILES}
    registry = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in schemas.values()
    )
    root = schemas["memory_event.schema.json"]
    jsonschema.Draft202012Validator.check_schema(root)
    return jsonschema.Draft202012Validator(root, registry=registry)


def validate_event(event: Any) -> dict[str, Any]:
    """Fail closed unless a stored record is a valid, verifier-consistent memory event."""
    if not isinstance(event, dict):
        raise VerificationError("memory event must be an object")
    errors = sorted(_memory_event_validator().iter_errors(event), key=lambda error: list(error.absolute_path))
    if errors:
        raise VerificationError("memory event violates schema: " + "; ".join(error.message for error in errors))
    output = event["output"]
    recomputed = verify_node_output(output)
    if recomputed["status"] != "passed":
        raise VerificationError("stored output no longer passes the node-output verifier")
    if event["verifier_result"] != recomputed:
        raise VerificationError("stored verifier_result does not match the recomputed result")
    if event["step_id"] != output["step_id"]:
        raise VerificationError("memory event step_id does not match its output")
    return event


def default_id_source() -> str:
    """Clock-independent IDs, distinct even when the clock is frozen."""
    return f"evt-{uuid.uuid4().hex}"


def default_clock() -> datetime:
    return datetime.now(timezone.utc)


class MemoryStore:
    """Small append-only episodic store with mechanically derived summaries."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        storage=None,
        id_source: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        if path is None and storage is None:
            raise ValueError("path or storage is required")
        self.path = Path(path) if path is not None else None
        self.storage = storage
        self.id_source = id_source if id_source is not None else default_id_source
        self.clock = clock if clock is not None else default_clock

    def _event_exists(self, event_id: str) -> bool:
        if self.storage is not None:
            return self.storage.get_event(event_id) is not None
        return any(event.get("event_id") == event_id for event in self.events())

    def append(self, *, node_id: str, round: int, objective: str, output: Any) -> dict[str, Any]:
        result = verify_node_output(output)
        if result["status"] != "passed":
            raise ValueError("unverified output cannot be committed")
        event_id = self.id_source()
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("id_source must return a non-empty string")
        if self._event_exists(event_id):
            raise ValueError(f"duplicate event_id: {event_id}")
        recorded_at = self.clock().isoformat()
        event = {"schema_version": "1.0", "event_id": event_id, "step_id": output["step_id"], "node_id": node_id, "round": round, "objective": objective, "output": output, "verifier_result": result, "recorded_at": recorded_at}
        if self.storage is not None:
            self.storage.append_event(event)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, sort_keys=True) + "\n")
        return event

    def events(self) -> list[dict[str, Any]]:
        if self.storage is not None:
            records = self.storage.list_events()
        elif not self.path.exists():
            return []
        else:
            records = []
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as error:
                    raise VerificationError("memory log contains an unreadable record") from error
        for record in records:
            validate_event(record)
        return records

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
        return consolidate(self.events())

    def should_consolidate(self, *, interval: int = 10) -> bool:
        """Deterministic backstop trigger; failure-pattern triggers can be added later."""
        if interval < 1:
            raise ValueError("interval must be positive")
        return bool(self.events()) and len(self.events()) % interval == 0
