"""SQLite-backed persistent cache for agenda and attachment data.

Uses in-memory dicts for reads (instant), SQLite for persistence across restarts.
"""

import json
import os
import sqlite3
import threading
from pathlib import Path

DB_PATH = Path(os.environ.get("CACHE_DB_PATH", Path(__file__).parent / "cache.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
ATTACHMENT_CACHE_VERSION = "2"

_local = threading.local()

# In-memory caches (loaded from SQLite at startup)
_agenda_mem: dict[str, list] = {}
_attachments_mem: dict[str, list] = {}
_summaries_mem: dict[str, dict] = {}  # key: "case_url::att_num"


def _get_conn() -> sqlite3.Connection:
    """Get a thread-local SQLite connection."""
    if not hasattr(_local, "conn"):
        _local.conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA synchronous=NORMAL")
        _init_db(_local.conn)
    return _local.conn


def _init_db(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cache_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agenda_cache (
            meeting_link TEXT PRIMARY KEY,
            items_json TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS attachments_cache (
            case_url TEXT PRIMARY KEY,
            attachments_json TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS summaries_cache (
            cache_key TEXT PRIMARY KEY,
            summary_json TEXT NOT NULL
        )
    """)
    row = conn.execute(
        "SELECT value FROM cache_metadata WHERE key = 'attachment_cache_version'"
    ).fetchone()
    if row is None or row[0] != ATTACHMENT_CACHE_VERSION:
        conn.execute("DELETE FROM attachments_cache")
        conn.execute(
            "INSERT OR REPLACE INTO cache_metadata (key, value) VALUES ('attachment_cache_version', ?)",
            (ATTACHMENT_CACHE_VERSION,),
        )
    conn.commit()


def _load_from_disk():
    """Load all cached data from SQLite into memory at startup."""
    conn = _get_conn()
    for row in conn.execute("SELECT meeting_link, items_json FROM agenda_cache"):
        _agenda_mem[row[0]] = json.loads(row[1])
    for row in conn.execute("SELECT case_url, attachments_json FROM attachments_cache"):
        _attachments_mem[row[0]] = json.loads(row[1])
    for row in conn.execute("SELECT cache_key, summary_json FROM summaries_cache"):
        _summaries_mem[row[0]] = json.loads(row[1])


# Load on import
_load_from_disk()


def get_agenda(meeting_link: str) -> list | None:
    return _agenda_mem.get(meeting_link)


def put_agenda(meeting_link: str, items: list):
    _agenda_mem[meeting_link] = items
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO agenda_cache (meeting_link, items_json) VALUES (?, ?)",
        (meeting_link, json.dumps(items, ensure_ascii=False)),
    )
    conn.commit()


def get_attachments(case_url: str) -> list | None:
    return _attachments_mem.get(case_url)


def put_attachments(case_url: str, attachments: list):
    _attachments_mem[case_url] = attachments
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO attachments_cache (case_url, attachments_json) VALUES (?, ?)",
        (case_url, json.dumps(attachments, ensure_ascii=False)),
    )
    conn.commit()


def invalidate_attachments(case_url: str):
    _attachments_mem.pop(case_url, None)
    conn = _get_conn()
    conn.execute("DELETE FROM attachments_cache WHERE case_url = ?", (case_url,))
    conn.commit()


def get_summary(case_url: str, attachment_num: int) -> dict | None:
    key = f"{case_url}::{attachment_num}"
    return _summaries_mem.get(key)


def put_summary(case_url: str, attachment_num: int, summary_data: dict):
    key = f"{case_url}::{attachment_num}"
    _summaries_mem[key] = summary_data
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO summaries_cache (cache_key, summary_json) VALUES (?, ?)",
        (key, json.dumps(summary_data, ensure_ascii=False)),
    )
    conn.commit()
