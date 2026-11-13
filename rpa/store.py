"""SQLite persistence: records table, outcomes table, resume progress."""

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no TEXT NOT NULL UNIQUE,
    amount REAL NOT NULL,
    remark TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS outcomes (
    record_id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('success', 'skipped', 'failed')),
    attempts INTEGER NOT NULL,
    last_error TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

FINAL_STATUSES = ("success", "skipped")


class Store:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    def completed(self):
        rows = self.conn.execute(
            "SELECT record_id FROM outcomes WHERE status IN ('success', 'skipped')"
        )
        return {row[0] for row in rows}

    def save_outcome(self, record_id, status, attempts, last_error, record=None):
        # record row + outcome row commit in one transaction: a crash either
        # persists both or neither, so a restart never double-submits.
        with self.conn:
            if record is not None and status in FINAL_STATUSES:
                self.conn.execute(
                    "INSERT OR IGNORE INTO records (order_no, amount, remark)"
                    " VALUES (?, ?, ?)",
                    (record["order_no"], float(record["amount"]), record["remark"]),
                )
            self.conn.execute(
                "INSERT INTO outcomes (record_id, status, attempts, last_error)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(record_id) DO UPDATE SET"
                "   status=excluded.status,"
                "   attempts=excluded.attempts,"
                "   last_error=excluded.last_error,"
                "   updated_at=datetime('now')",
                (record_id, status, attempts, last_error or "none"),
            )

    def tally(self, record_ids):
        counts = {"success": 0, "skipped": 0, "failed": 0}
        if not record_ids:
            return counts
        placeholders = ",".join("?" for _ in record_ids)
        rows = self.conn.execute(
            f"SELECT status, COUNT(*) FROM outcomes"
            f" WHERE record_id IN ({placeholders}) GROUP BY status",
            record_ids,
        )
        for status, count in rows:
            counts[status] = count
        return counts

    def close(self):
        self.conn.close()

