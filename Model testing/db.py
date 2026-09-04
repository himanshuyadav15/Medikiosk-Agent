"""
MediKiosk — SQLite persistence layer for clinical summaries.

Stores every completed patient interview summary so the Doctor Dashboard
can retrieve them later via the API.
"""

import sqlite3
import json
import os
from datetime import datetime, timezone
from typing import Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "medikiosk.db")


def _get_conn() -> sqlite3.Connection:
    """Return a connection with row_factory set so rows behave like dicts."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create the summaries table if it doesn't already exist."""
    conn = _get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS summaries (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            patient_name  TEXT    NOT NULL,
            summary_json  TEXT    NOT NULL,   -- full structured data as JSON string
            summary_text  TEXT    NOT NULL,   -- formatted clinical note for display
            red_flag      TEXT,               -- nullable; e.g. "Blood in sputum"
            source        TEXT    NOT NULL DEFAULT 'text',  -- 'text' or 'voice'
            created_at    TEXT    NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def save_summary(
    patient_name: str,
    summary_json: dict,
    summary_text: str,
    red_flag: Optional[str] = None,
    source: str = "text",
) -> int:
    """
    Persist a completed interview summary.
    Returns the auto-generated row ID.
    """
    conn = _get_conn()
    cursor = conn.execute(
        """
        INSERT INTO summaries (patient_name, summary_json, summary_text, red_flag, source, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            patient_name,
            json.dumps(summary_json),
            summary_text,
            red_flag,
            source,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    row_id = cursor.lastrowid
    conn.close()
    return row_id


def get_summary(summary_id: int) -> Optional[dict]:
    """Fetch a single summary by ID. Returns None if not found."""
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM summaries WHERE id = ?", (summary_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return _row_to_dict(row)


def get_all_summaries() -> list[dict]:
    """Return all summaries, newest first."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM summaries ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def _row_to_dict(row: sqlite3.Row) -> dict:
    """Convert a sqlite3.Row to a plain dict, parsing summary_json back."""
    d = dict(row)
    d["summary_json"] = json.loads(d["summary_json"])
    return d
