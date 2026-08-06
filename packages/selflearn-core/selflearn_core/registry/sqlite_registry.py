"""SQLite-backed model registry (Gate 3).

Uses the ``registry`` table from ``store/schema.sql`` plus a small side table
for recorded live scores. Promotion and rollback are manual gates — this class
only changes status when explicitly told to; nothing here is called by the
updater automatically.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any, Callable, Optional

from ..types import Score
from .registry import Registry

_SCORES_TABLE = """
CREATE TABLE IF NOT EXISTS registry_scores (
    version     TEXT NOT NULL REFERENCES registry(version),
    recorded_ts INTEGER NOT NULL,
    score       TEXT NOT NULL              -- JSON-serialized Score
);
CREATE INDEX IF NOT EXISTS idx_regscores_version ON registry_scores(version, recorded_ts);
"""


class SqliteRegistry(Registry):
    def __init__(self, path: str = "data/selflearn.sqlite", now: Callable[[], int] = None) -> None:
        self._path = path
        self._now = now or (lambda: int(time.time()))
        with self._connect() as con:
            con.executescript(_SCORES_TABLE)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        return con

    # -- Registry ABC ------------------------------------------------------

    def register(self, task: str, config: dict[str, Any]) -> str:
        version = str(uuid.uuid4())
        with self._connect() as con:
            con.execute(
                "INSERT INTO registry (version, task, config, created_ts, status, autonomy)"
                " VALUES (?, ?, ?, ?, 'candidate', 0)",
                (version, task, json.dumps(config), self._now()),
            )
        return version

    def get(self, version: str) -> Optional[dict[str, Any]]:
        with self._connect() as con:
            row = con.execute("SELECT config FROM registry WHERE version = ?", (version,)).fetchone()
        return json.loads(row["config"]) if row else None

    def live_scores(self, task: str) -> list[Score]:
        """Most recently recorded score set for the task's live version."""
        with self._connect() as con:
            live = con.execute(
                "SELECT version FROM registry WHERE task = ? AND status = 'live'", (task,)
            ).fetchone()
            if not live:
                return []
            rows = con.execute(
                "SELECT score FROM registry_scores WHERE version = ? AND recorded_ts ="
                " (SELECT MAX(recorded_ts) FROM registry_scores WHERE version = ?)",
                (live["version"], live["version"]),
            ).fetchall()
        return [Score(**json.loads(r["score"])) for r in rows]

    def promote(self, version: str) -> None:
        """Manual gate: mark a candidate live; the previous live is retired."""
        with self._connect() as con:
            row = con.execute("SELECT task, status FROM registry WHERE version = ?", (version,)).fetchone()
            if row is None:
                raise KeyError(f"unknown version {version}")
            con.execute(
                "UPDATE registry SET status = 'retired' WHERE task = ? AND status = 'live'",
                (row["task"],),
            )
            con.execute("UPDATE registry SET status = 'live' WHERE version = ?", (version,))

    def rollback(self, task: str) -> None:
        """Manual gate: retire the live config and revive the most recently
        created previously-retired one."""
        with self._connect() as con:
            prev = con.execute(
                "SELECT version FROM registry WHERE task = ? AND status = 'retired'"
                " ORDER BY created_ts DESC LIMIT 1",
                (task,),
            ).fetchone()
            if prev is None:
                raise LookupError(f"no retired config to roll back to for task {task!r}")
            con.execute(
                "UPDATE registry SET status = 'retired' WHERE task = ? AND status = 'live'", (task,)
            )
            con.execute("UPDATE registry SET status = 'live' WHERE version = ?", (prev["version"],))

    # -- extras (not part of the ABC) ---------------------------------------

    def record_scores(self, version: str, scores: list[Score]) -> None:
        """Attach the latest scored snapshot to a version (called by the scorer)."""
        ts = self._now()
        with self._connect() as con:
            con.executemany(
                "INSERT INTO registry_scores (version, recorded_ts, score) VALUES (?, ?, ?)",
                [(version, ts, json.dumps(s.__dict__)) for s in scores],
            )

    def status(self, version: str) -> Optional[str]:
        with self._connect() as con:
            row = con.execute("SELECT status FROM registry WHERE version = ?", (version,)).fetchone()
        return row["status"] if row else None
