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
            connection.execute("CREATE TABLE IF NOT EXISTS event_chain (position INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE, link TEXT NOT NULL)")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def append_event(self, event: dict[str, Any], *, chain_link: str | None = None) -> None:
        """Insert an event; with ``chain_link``, record its hash-chain link in the same transaction."""
        payload = json.dumps(event, sort_keys=True)
        try:
            with self._connect() as connection:
                connection.execute("INSERT INTO events(event_id, recorded_at, payload) VALUES (?, ?, ?)", (event["event_id"], event["recorded_at"], payload))
                if chain_link is not None:
                    connection.execute("INSERT INTO event_chain(event_id, link) VALUES (?, ?)", (event["event_id"], chain_link))
        except sqlite3.IntegrityError as error:
            raise ValueError(f"duplicate event_id: {event['event_id']}") from error

    def append_chain_link(self, event_id: str, link: str) -> None:
        try:
            with self._connect() as connection:
                connection.execute("INSERT INTO event_chain(event_id, link) VALUES (?, ?)", (event_id, link))
        except sqlite3.IntegrityError as error:
            raise ValueError(f"duplicate chain link for event_id: {event_id}") from error

    def list_chain_links(self) -> list[tuple[str, str]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT event_id, link FROM event_chain ORDER BY position").fetchall()
        return [(row["event_id"], row["link"]) for row in rows]

    def list_events(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT payload FROM events ORDER BY rowid").fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM events WHERE event_id = ?", (event_id,)).fetchone()
        return json.loads(row["payload"]) if row else None
