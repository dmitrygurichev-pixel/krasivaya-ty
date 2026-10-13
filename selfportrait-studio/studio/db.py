"""Очередь заданий в SQLite — переживает перезапуски и перебои сети."""
from __future__ import annotations

import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  key TEXT UNIQUE NOT NULL,
  src TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'new',      -- new | processed | uploaded | failed
  processed_path TEXT,
  remote_path TEXT,
  public_url TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  next_try REAL NOT NULL DEFAULT 0,
  created REAL NOT NULL
);
"""


class JobDB:
    def __init__(self, path: str):
        self.c = sqlite3.connect(path)
        self.c.row_factory = sqlite3.Row
        self.c.executescript(SCHEMA)

    def add(self, key: str, src: str) -> bool:
        cur = self.c.execute(
            "INSERT OR IGNORE INTO jobs(key, src, created) VALUES (?,?,?)", (key, src, time.time())
        )
        self.c.commit()
        return cur.rowcount == 1

    def due(self, status: str) -> list[sqlite3.Row]:
        return self.c.execute(
            "SELECT * FROM jobs WHERE status=? AND next_try<=? ORDER BY id", (status, time.time())
        ).fetchall()

    def update(self, job_id: int, **fields) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        self.c.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))
        self.c.commit()

    def counts(self) -> dict[str, int]:
        return {r[0]: r[1] for r in self.c.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status")}
