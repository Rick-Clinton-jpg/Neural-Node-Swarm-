from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class SQLiteStorage:
    """Transactional append-only event storage."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        with self._connect() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS events (event_id TEXT PRIMARY KEY, recorded_at TEXT NOT NULL, payload TEXT NOT NULL)")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def append_event(self, event: dict[str, Any]) -> None:
        payload = json.dumps(event, sort_keys=True)
        with self._connect() as connection:
            connection.execute("INSERT INTO events(event_id, recorded_at, payload) VALUES (?, ?, ?)", (event["event_id"], event["recorded_at"], payload))

    def list_events(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT payload FROM events ORDER BY rowid").fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM events WHERE event_id = ?", (event_id,)).fetchone()
        return json.loads(row["payload"]) if row else None
