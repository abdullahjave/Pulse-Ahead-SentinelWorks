"""Persistent decision / acknowledgement records (SQLite, standard library only)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from . import config as C

DB_PATH = C.DATA_DIR / "state.db"


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_key TEXT NOT NULL, incident_id TEXT NOT NULL, status TEXT NOT NULL,
            note TEXT, role TEXT NOT NULL, user_id TEXT NOT NULL,
            replay_date TEXT NOT NULL, recorded_at TEXT NOT NULL)"""
    )
    return conn


def add_decision(run_key, incident_id, status, note, role, user_id, replay_date) -> dict:
    rec = {"run_key": run_key, "incident_id": incident_id, "status": status, "note": (note or "")[:500],
           "role": role, "user_id": user_id, "replay_date": replay_date,
           "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    with _conn() as c:
        cur = c.execute("INSERT INTO decisions (run_key, incident_id, status, note, role, user_id, replay_date, recorded_at) VALUES (?,?,?,?,?,?,?,?)",
                        tuple(rec.values()))
        rec["id"] = cur.lastrowid
    return rec


def decisions(run_key, incident_id=None) -> list[dict]:
    q = "SELECT * FROM decisions WHERE run_key = ?"
    args = [run_key]
    if incident_id:
        q += " AND incident_id = ?"
        args.append(incident_id)
    with _conn() as c:
        return [dict(r) for r in c.execute(q + " ORDER BY id", args)]


def reset() -> int:
    with _conn() as c:
        n = c.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
        c.execute("DELETE FROM decisions")
    return n
