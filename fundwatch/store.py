"""SQLite persistence: runs, signals (with hysteresis history) and quotes."""
import json
import logging
import sqlite3
from pathlib import Path

log = logging.getLogger("fundwatch.store")

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  date TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  ts TEXT NOT NULL,
  note TEXT
);
CREATE TABLE IF NOT EXISTS signals (
  date TEXT NOT NULL,
  code TEXT NOT NULL,
  score REAL,
  raw_action TEXT,
  final_action TEXT,
  amount REAL,
  reasons TEXT,
  PRIMARY KEY (date, code)
);
CREATE TABLE IF NOT EXISTS quotes (
  date TEXT NOT NULL,
  code TEXT NOT NULL,
  nav REAL,
  est_nav REAL,
  est_chg REAL,
  source TEXT,
  PRIMARY KEY (date, code)
);
"""


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        try:
            self.conn.close()
        except sqlite3.Error:
            pass

    def has_success(self, date):
        row = self.conn.execute(
            "SELECT status FROM runs WHERE date = ?", (date,)).fetchone()
        return bool(row and row["status"] == "ok")

    def save_run(self, date, status, ts, note=""):
        self.conn.execute(
            "INSERT INTO runs(date, status, ts, note) VALUES(?,?,?,?) "
            "ON CONFLICT(date) DO UPDATE SET status=excluded.status, "
            "ts=excluded.ts, note=excluded.note",
            (date, status, ts, note))
        self.conn.commit()

    def save_signal(self, date, code, score, raw_action, final_action, amount, reasons):
        self.conn.execute(
            "INSERT INTO signals(date, code, score, raw_action, final_action, amount, reasons) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(date, code) DO UPDATE SET "
            "score=excluded.score, raw_action=excluded.raw_action, "
            "final_action=excluded.final_action, amount=excluded.amount, "
            "reasons=excluded.reasons",
            (date, code, score, raw_action, final_action, amount,
             json.dumps(reasons, ensure_ascii=False)))
        self.conn.commit()

    def last_signal(self, code, before_date):
        row = self.conn.execute(
            "SELECT * FROM signals WHERE code = ? AND date < ? "
            "ORDER BY date DESC LIMIT 1", (code, before_date)).fetchone()
        return dict(row) if row else None

    def save_quote(self, date, code, nav, est_nav, est_chg, source):
        self.conn.execute(
            "INSERT INTO quotes(date, code, nav, est_nav, est_chg, source) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(date, code) DO UPDATE SET "
            "nav=excluded.nav, est_nav=excluded.est_nav, "
            "est_chg=excluded.est_chg, source=excluded.source",
            (date, code, nav, est_nav, est_chg, source))
        self.conn.commit()

    def previous_signal(self, code, before_date):
        rows = self.conn.execute(
            "SELECT * FROM signals WHERE code = ? AND date < ? "
            "ORDER BY date DESC LIMIT 2", (code, before_date)).fetchall()
        return [dict(row) for row in rows]
