"""Persistent decision / acknowledgement records (SQLite, standard library only).

Recording needs a writable database. Locally that is backend/data/state.db (or SENTINEL_DB_PATH). On a hosted
serverless deployment (Vercel) the filesystem is read-only and not shared between instances, so recording is
disabled and the app runs in a clearly labelled read-only demo mode: nothing here touches the filesystem, reads
return no records, and writes raise ReadOnlyMode. Set SENTINEL_DB_PATH to a writable file to force recording on,
or SENTINEL_READ_ONLY=1 to force it off.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import config as C
from .auth import hosted

READ_ONLY_REASON = ("This is a hosted read-only demo: the serverless filesystem is read-only and not shared between "
                    "instances, so responses cannot be saved. Run SentinelWorks locally (or attach a database) to record responses.")


class ReadOnlyMode(RuntimeError):
    pass


def db_path() -> Path:
    return Path(os.environ.get("SENTINEL_DB_PATH") or (C.DATA_DIR / "state.db"))


def recording_enabled() -> bool:
    flag = os.environ.get("SENTINEL_READ_ONLY", "").strip().lower()
    if flag in ("1", "true", "yes"):
        return False
    if flag in ("0", "false", "no"):
        return True
    return not (hosted() and not os.environ.get("SENTINEL_DB_PATH"))


def _conn():
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_key TEXT NOT NULL, incident_id TEXT NOT NULL, status TEXT NOT NULL,
            note TEXT, role TEXT NOT NULL, user_id TEXT NOT NULL,
            replay_date TEXT NOT NULL, recorded_at TEXT NOT NULL)"""
    )
    return conn


def _require_writable():
    if not recording_enabled():
        raise ReadOnlyMode(READ_ONLY_REASON)


def add_decision(run_key, incident_id, status, note, role, user_id, replay_date) -> dict:
    _require_writable()
    rec = {"run_key": run_key, "incident_id": incident_id, "status": status, "note": (note or "")[:500],
           "role": role, "user_id": user_id, "replay_date": replay_date,
           "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    with _conn() as c:
        cur = c.execute("INSERT INTO decisions (run_key, incident_id, status, note, role, user_id, replay_date, recorded_at) VALUES (?,?,?,?,?,?,?,?)",
                        tuple(rec.values()))
        rec["id"] = cur.lastrowid
    return rec


def decisions(run_key, incident_id=None) -> list[dict]:
    """Read-only access: never creates the database or its directory, so it is safe on a read-only filesystem.
    No database means no records."""
    if not recording_enabled():
        return []
    path = db_path()
    if not path.exists():
        return []
    q = "SELECT * FROM decisions WHERE run_key = ?"
    args = [run_key]
    if incident_id:
        q += " AND incident_id = ?"
        args.append(incident_id)
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(q + " ORDER BY id", args)]
        finally:
            conn.close()
    except sqlite3.OperationalError:  # file exists but the table has not been created yet
        return []


def reset() -> int:
    _require_writable()
    with _conn() as c:
        n = c.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
        c.execute("DELETE FROM decisions")
    return n
