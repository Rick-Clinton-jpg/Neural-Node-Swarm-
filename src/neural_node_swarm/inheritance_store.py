"""SQLite inheritance boundary for the numeric regional experiment.

Trusted host supplies evidence. This is not general semantic verification or an
authorization layer; callers must not expose this host API directly to workers.
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

from jsonschema import Draft202012Validator, FormatChecker


def schema(kind):
    properties = {
        "id": {"type": "string", "format": "uuid"},
        "kind": {"const": kind}, "run_id": {"type": "string", "minLength": 1},
        "region": {"enum": ["hand", "leg"]},
        "recorded_at": {"type": "string", "format": "date-time"},
    }
    if kind == "active":
        properties.update(value={"type": "integer", "minimum": 0, "maximum": 100},
                          evidence_ref={"type": "string", "minLength": 1})
    else:
        properties["source_id"] = {"type": "string", "format": "uuid"}
        if kind == "audit":
            properties.update(passed={"type": "boolean"}, rule={"const": "evidence_equal_v1"},
                              expected={"type": ["integer", "null"]})
        else:
            properties["promotion_id"] = {"type": "string", "format": "uuid"}
    return {"type": "object", "additionalProperties": False,
            "required": list(properties), "properties": properties}


class InheritanceStore:
    def __init__(self, path):
        self.path = str(path)
        with self.connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS inheritance_records "
                       "(seq INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL, "
                       "kind TEXT NOT NULL, run_id TEXT NOT NULL, region TEXT NOT NULL, payload TEXT NOT NULL)")
            for action in ("UPDATE", "DELETE"):
                db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{action} BEFORE {action} "
                           "ON inheritance_records BEGIN SELECT RAISE(ABORT, 'append only'); END")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path)
        try:
            with db:
                yield db
        finally:
            db.close()

    def _record(self, kind, run_id, region, **fields):
        return dict(id=str(uuid4()), kind=kind, run_id=run_id, region=region,
                    recorded_at=datetime.now(timezone.utc).isoformat(), **fields)

    def _insert(self, db, record):
        Draft202012Validator(schema(record["kind"]), format_checker=FormatChecker()).validate(record)
        db.execute("INSERT INTO inheritance_records(id,kind,run_id,region,payload) VALUES (?,?,?,?,?)",
                   (record["id"], record["kind"], record["run_id"], record["region"], json.dumps(record)))

    def accept(self, run_id, region, value, evidence_ref):
        record = self._record("active", run_id, region, value=value, evidence_ref=evidence_ref)
        with self.connection() as db:
            self._insert(db, record)
        return record

    def read(self, namespace, run_id, region, event_id=None):
        if namespace not in {"active", "lineage", "audit"}:
            raise ValueError("unknown namespace")
        with self.connection() as db:
            query = "SELECT payload FROM inheritance_records WHERE kind=? AND run_id=? AND region=?"
            args = [namespace, run_id, region]
            if event_id is not None:
                query += " AND id=?"
                args.append(event_id)
            row = db.execute(query + " ORDER BY seq DESC LIMIT 1", args).fetchone()
        return json.loads(row[0]) if row else None

    def promote(self, run_id, region, source_id, evidence):
        source = self.read("active", run_id, region, source_id)
        if source is None:
            raise KeyError("active source missing in requested scope")
        expected = evidence.get(source["evidence_ref"])
        passed = type(expected) is int and expected == source["value"]
        audit = self._record("audit", run_id, region, source_id=source_id,
                             passed=passed, rule="evidence_equal_v1", expected=expected)
        lineage = self._record("lineage", run_id, region, source_id=source_id,
                               promotion_id=audit["id"]) if passed else None
        with self.connection() as db:
            self._insert(db, audit)
            if lineage:
                self._insert(db, lineage)
        return audit, lineage
