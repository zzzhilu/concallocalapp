"""
ConCall — Meeting Persistence (SQLite)

Stores meeting records (transcripts, translations, summaries) in a local
SQLite database that is mounted as a Docker volume for durability.
"""

import json
import os
import sqlite3
import uuid
from datetime import datetime
from typing import Optional

DB_DIR = os.getenv("DATA_DIR", "/app/data")
DB_PATH = os.path.join(DB_DIR, "meetings.db")


def _connect() -> sqlite3.Connection:
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """Create the meetings table if it does not exist."""
    conn = _connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS meetings (
            id          TEXT PRIMARY KEY,
            title       TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            duration    INTEGER DEFAULT 0,
            mode        TEXT DEFAULT 'zh',
            transcripts TEXT DEFAULT '[]',
            translations TEXT DEFAULT '[]',
            summary     TEXT DEFAULT '',
            speakers    TEXT DEFAULT '{}'
        )
    """)
    conn.commit()
    conn.close()


def save_meeting(
    title: str,
    duration: int,
    mode: str,
    transcripts: list,
    translations: list,
    summary: str,
    speakers: dict,
    meeting_id: Optional[str] = None,
) -> dict:
    """Save a meeting record and return the saved row as dict."""
    if not meeting_id:
        meeting_id = str(uuid.uuid4())
    created_at = datetime.now().isoformat()

    conn = _connect()
    conn.execute(
        """
        INSERT OR REPLACE INTO meetings
            (id, title, created_at, duration, mode, transcripts, translations, summary, speakers)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            meeting_id,
            title,
            created_at,
            duration,
            mode,
            json.dumps(transcripts, ensure_ascii=False),
            json.dumps(translations, ensure_ascii=False),
            summary,
            json.dumps(speakers, ensure_ascii=False),
        ),
    )
    conn.commit()
    conn.close()

    return {
        "id": meeting_id,
        "title": title,
        "created_at": created_at,
        "duration": duration,
        "mode": mode,
    }


def list_meetings() -> list[dict]:
    """Return all meetings (metadata only, no heavy content)."""
    conn = _connect()
    rows = conn.execute(
        "SELECT id, title, created_at, duration, mode FROM meetings ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_meeting(meeting_id: str) -> Optional[dict]:
    """Return a full meeting record by id."""
    conn = _connect()
    row = conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
    conn.close()
    if not row:
        return None
    result = dict(row)
    result["transcripts"] = json.loads(result["transcripts"])
    result["translations"] = json.loads(result["translations"])
    result["speakers"] = json.loads(result["speakers"])
    return result


def delete_meeting(meeting_id: str) -> bool:
    """Delete a meeting by id. Returns True if a row was deleted."""
    conn = _connect()
    cursor = conn.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    return deleted
