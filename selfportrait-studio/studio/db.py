"""Очередь заданий в SQLite — переживает перезапуски и перебои сети."""
from __future__ import annotations

import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  key TEXT UNIQUE NOT NULL,
  src TEXT NOT NULL,
  variant TEXT NOT NULL DEFAULT '',
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
        # окно читает счётчики из другого потока, поэтому соединение общее, доступ под замком
        self.c = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.RLock()
        self.c.row_factory = sqlite3.Row
        self.c.executescript(SCHEMA)
        try:  # база от старой версии без вариантов
            self.c.execute("ALTER TABLE jobs ADD COLUMN variant TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError:
            pass

    def add(self, key: str, src: str, variant: str = "") -> bool:
        with self.lock:
            cur = self.c.execute(
                "INSERT OR IGNORE INTO jobs(key, src, variant, created) VALUES (?,?,?,?)",
                (key, src, variant, time.time()),
            )
            self.c.commit()
            return cur.rowcount == 1

    def due(self, status: str) -> list[sqlite3.Row]:
        with self.lock:
            return self.c.execute(
                "SELECT * FROM jobs WHERE status=? AND next_try<=? ORDER BY id", (status, time.time())
            ).fetchall()

    def update(self, job_id: int, **fields) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        with self.lock:
            self.c.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))
            self.c.commit()

    def src_statuses(self, src: str) -> list[str]:
        with self.lock:
            return [r[0] for r in self.c.execute("SELECT status FROM jobs WHERE src=?", (src,))]

    def counts(self) -> dict[str, int]:
        with self.lock:
            return {r[0]: r[1] for r in self.c.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status")}
