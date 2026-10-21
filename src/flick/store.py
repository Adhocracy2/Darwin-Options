"""SQLite journal: positions, events, equity snapshots and scan results."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .models import Position

SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
  id TEXT PRIMARY KEY, status TEXT NOT NULL, strategy TEXT, underlying TEXT,
  opened_at TEXT, closed_at TEXT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, kind TEXT NOT NULL,
  position_id TEXT, message TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS snapshots (
  ts TEXT PRIMARY KEY, equity REAL, open_risk REAL, realized REAL, unrealized REAL, data TEXT);
CREATE TABLE IF NOT EXISTS scans (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY, value TEXT NOT NULL, version INTEGER NOT NULL, updated_at TEXT NOT NULL);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._mem = sqlite3.connect(":memory:", check_same_thread=False) if path == ":memory:" else None
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = self._mem or sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            if self._mem is None:
                conn.close()

    def save_position(self, p: Position) -> None:
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO positions VALUES (?,?,?,?,?,?,?)",
                      (p.id, p.status, p.strategy, p.underlying, p.opened_at, p.closed_at,
                       json.dumps(p.to_dict())))

    def positions(self, status: str | None = "open") -> list[Position]:
        q, args = "SELECT data FROM positions", ()
        if status:
            q, args = q + " WHERE status = ?", (status,)
        with self._conn() as c:
            rows = c.execute(q + " ORDER BY opened_at", args).fetchall()
        return [Position.from_dict(json.loads(r["data"])) for r in rows]

    def get_position(self, pid: str) -> Position | None:
        with self._conn() as c:
            r = c.execute("SELECT data FROM positions WHERE id = ?", (pid,)).fetchone()
        return Position.from_dict(json.loads(r["data"])) if r else None

    def event(self, kind: str, message: str, position_id: str | None = None, data: dict | None = None) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO events (ts, kind, position_id, message, data) VALUES (?,?,?,?,?)",
                      (now(), kind, position_id, message, json.dumps(data or {}, default=str)))

    def events(self, limit: int = 200) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) | {"data": json.loads(r["data"] or "{}")} for r in rows]

    def snapshot(self, equity: float, open_risk: float, realized: float, unrealized: float,
                 data: dict | None = None) -> None:
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?,?,?)",
                      (now(), equity, open_risk, realized, unrealized, json.dumps(data or {})))

    def snapshots(self, limit: int = 5000) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT ts, equity, open_risk, realized, unrealized FROM snapshots "
                             "ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def get_setting(self, key: str) -> tuple[dict, int]:
        """(value, version). Version 0 means never saved."""
        with self._conn() as c:
            r = c.execute("SELECT value, version FROM settings WHERE key = ?", (key,)).fetchone()
        return (json.loads(r["value"]), r["version"]) if r else ({}, 0)

    def set_setting(self, key: str, value: dict) -> int:
        with self._conn() as c:
            r = c.execute("SELECT version FROM settings WHERE key = ?", (key,)).fetchone()
            version = (r["version"] if r else 0) + 1
            c.execute("INSERT OR REPLACE INTO settings VALUES (?,?,?,?)", (key, json.dumps(value), version, now()))
        return version

    def save_scan(self, rows: list[dict]) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO scans (ts, data) VALUES (?, ?)", (now(), json.dumps(rows, default=str)))
            c.execute("DELETE FROM scans WHERE id NOT IN (SELECT id FROM scans ORDER BY id DESC LIMIT 50)")

    def latest_scan(self) -> dict:
        with self._conn() as c:
            r = c.execute("SELECT ts, data FROM scans ORDER BY id DESC LIMIT 1").fetchone()
        return {"ts": r["ts"], "candidates": json.loads(r["data"])} if r else {"ts": None, "candidates": []}
