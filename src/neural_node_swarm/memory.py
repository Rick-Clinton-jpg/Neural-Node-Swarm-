from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .verifier import verify_node_output
from .consolidation import consolidate
from .schemas import load_schema


class UntrustedMemoryError(ValueError):
    """Raised when a stored record fails validation on read and cannot be trusted."""


_EVENT_VALIDATOR = None


def _event_validator():
    global _EVENT_VALIDATOR
    if _EVENT_VALIDATOR is None:
        import jsonschema
        from referencing import Registry, Resource

        resources = []
        for name in ("memory_event", "node_output", "verifier_result"):
            schema = load_schema(name)
            resources.append((schema["$id"], Resource.from_contents(schema)))
        registry = Registry().with_resources(resources)
        event_schema = load_schema("memory_event")
        _EVENT_VALIDATOR = jsonschema.Draft202012Validator(event_schema, registry=registry)
    return _EVENT_VALIDATOR


def validate_stored_event(event: Any) -> list[str]:
    """Return the reasons a stored record cannot be trusted; empty when it can."""
    if not isinstance(event, dict):
        return ["record is not an object"]
    errors = [error.message for error in _event_validator().iter_errors(event)]
    if errors:
        return errors
    recomputed = verify_node_output(event["output"])
    if recomputed["status"] != "passed":
        errors.append("stored output no longer passes the node-output verifier")
    if event["verifier_result"] != recomputed:
        errors.append("stored verifier_result does not match re-verification")
    if event["step_id"] != event["output"]["step_id"]:
        errors.append("event step_id does not match output step_id")
    return errors


def _trusted(events: list[Any]) -> list[dict[str, Any]]:
    for index, event in enumerate(events):
        errors = validate_stored_event(event)
        if errors:
            event_id = event.get("event_id") if isinstance(event, dict) else None
            raise UntrustedMemoryError(f"untrusted memory record at index {index} ({event_id!r}): {'; '.join(errors)}")
    return events


def _default_id_source() -> str:
    return f"evt-{uuid.uuid4().hex}"


def _default_clock() -> datetime:
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
        self.id_source = id_source if id_source is not None else _default_id_source
        self.clock = clock if clock is not None else _default_clock

    def append(self, *, node_id: str, round: int, objective: str, output: Any) -> dict[str, Any]:
        result = verify_node_output(output)
        if result["status"] != "passed":
            raise ValueError("unverified output cannot be committed")
        event_id = self.id_source()
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("id_source must return a non-empty string")
        event = {"schema_version": "1.0", "event_id": event_id, "step_id": output["step_id"], "node_id": node_id, "round": round, "objective": objective, "output": output, "verifier_result": result, "recorded_at": self.clock().isoformat()}
        if self.storage is not None:
            self.storage.append_event(event)
        else:
            if any(existing["event_id"] == event_id for existing in self.events()):
                raise ValueError(f"duplicate event_id: {event_id}")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, sort_keys=True) + "\n")
        return event

    def events(self) -> list[dict[str, Any]]:
        """Return every stored record, validated on read; fail closed on any untrusted record."""
        if self.storage is not None:
            return _trusted(self.storage.list_events())
        if not self.path.exists():
            return []
        records = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise UntrustedMemoryError(f"unparseable memory record: {error}") from error
        return _trusted(records)

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
