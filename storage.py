"""
SQLite storage for YouTube Channel Analyzer.
Handles: search history, pinned channels, channel snapshots,
         alert configs, SMTP config.
"""
import sqlite3
import os
import threading
from datetime import datetime
from typing import Optional

_BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_BASE, "data", "analyzer.db")
_lock = threading.Lock()


def _conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def _add_column_if_missing(db, table: str, col: str, definition: str):
    """ALTER TABLE only if the column doesn't exist yet (idempotent migration)."""
    existing = [r[1] for r in db.execute(f"PRAGMA table_info({table})").fetchall()]
    if col not in existing:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")


def init_db():
    with _lock, _conn() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS search_history (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            query         TEXT    NOT NULL,
            channel_id    TEXT,
            channel_name  TEXT,
            thumbnail_url TEXT,
            searched_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS pinned_channels (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id    TEXT    NOT NULL UNIQUE,
            channel_name  TEXT    NOT NULL,
            query         TEXT    NOT NULL,
            thumbnail_url TEXT,
            pinned_at     DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS channel_snapshots (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id   TEXT    NOT NULL,
            channel_name TEXT,
            subscribers  INTEGER,
            total_views  INTEGER,
            avg_views    REAL,
            eng_rate     REAL,
            video_count  INTEGER,
            snapshot_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_snapshots
            ON channel_snapshots(channel_id, snapshot_at DESC);

        CREATE TABLE IF NOT EXISTS alert_configs (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id       TEXT    NOT NULL UNIQUE,
            email            TEXT    NOT NULL,
            spike_threshold  REAL    DEFAULT 50.0,
            drop_threshold   REAL    DEFAULT 40.0,
            check_frequency  TEXT    DEFAULT 'daily',
            enabled          INTEGER DEFAULT 1,
            updated_at       DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS smtp_config (
            id        INTEGER PRIMARY KEY DEFAULT 1,
            host      TEXT    DEFAULT '',
            port      INTEGER DEFAULT 587,
            username  TEXT    DEFAULT '',
            password  TEXT    DEFAULT '',
            use_tls   INTEGER DEFAULT 1,
            from_addr TEXT    DEFAULT '',
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """)
        # Idempotent schema migrations
        _add_column_if_missing(db, "pinned_channels", "tags", "TEXT DEFAULT ''")
        _add_column_if_missing(db, "pinned_channels", "last_video_id", "TEXT DEFAULT ''")
        _add_column_if_missing(db, "smtp_config", "webhook_url", "TEXT DEFAULT ''")


# ── Search History ─────────────────────────────────────────────────────────

def add_search(query: str, channel_id: str, channel_name: str, thumbnail_url: str):
    with _lock, _conn() as db:
        db.execute(
            "INSERT INTO search_history (query, channel_id, channel_name, thumbnail_url) "
            "VALUES (?, ?, ?, ?)",
            (query, channel_id, channel_name, thumbnail_url)
        )


def get_history(limit: int = 30) -> list[dict]:
    """Return most recent searches, one entry per channel_id."""
    with _conn() as db:
        rows = db.execute(
            "SELECT query, channel_id, channel_name, thumbnail_url, MAX(searched_at) as searched_at "
            "FROM search_history GROUP BY channel_id "
            "ORDER BY searched_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def clear_history():
    with _lock, _conn() as db:
        db.execute("DELETE FROM search_history")


# ── Pinned Channels ────────────────────────────────────────────────────────

def pin_channel(channel_id: str, channel_name: str, query: str, thumbnail_url: str):
    with _lock, _conn() as db:
        db.execute(
            "INSERT OR REPLACE INTO pinned_channels "
            "(channel_id, channel_name, query, thumbnail_url) VALUES (?, ?, ?, ?)",
            (channel_id, channel_name, query, thumbnail_url)
        )


def unpin_channel(channel_id: str):
    with _lock, _conn() as db:
        db.execute("DELETE FROM pinned_channels WHERE channel_id = ?", (channel_id,))


def get_pinned() -> list[dict]:
    with _conn() as db:
        rows = db.execute(
            "SELECT * FROM pinned_channels ORDER BY pinned_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def is_pinned(channel_id: str) -> bool:
    with _conn() as db:
        row = db.execute(
            "SELECT 1 FROM pinned_channels WHERE channel_id = ?", (channel_id,)
        ).fetchone()
    return row is not None


# ── Channel Snapshots ──────────────────────────────────────────────────────

def save_snapshot(channel_id: str, channel_name: str, subscribers: int,
                  total_views: int, avg_views: float, eng_rate: float,
                  video_count: int):
    with _lock, _conn() as db:
        db.execute(
            "INSERT INTO channel_snapshots "
            "(channel_id, channel_name, subscribers, total_views, avg_views, eng_rate, video_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (channel_id, channel_name, subscribers, total_views, avg_views, eng_rate, video_count)
        )


def get_latest_snapshots(channel_id: str, n: int = 2) -> list[dict]:
    with _conn() as db:
        rows = db.execute(
            "SELECT * FROM channel_snapshots WHERE channel_id = ? "
            "ORDER BY snapshot_at DESC LIMIT ?",
            (channel_id, n)
        ).fetchall()
    return [dict(r) for r in rows]


def get_all_pinned_with_snapshots() -> list[dict]:
    """Return all pinned channels that have at least 2 snapshots."""
    pinned = get_pinned()
    result = []
    for p in pinned:
        snaps = get_latest_snapshots(p["channel_id"], 2)
        if len(snaps) >= 2:
            result.append({"channel": p, "snapshots": snaps})
    return result


# ── Alert Configs ──────────────────────────────────────────────────────────

def set_alert_config(channel_id: str, email: str, spike_threshold: float,
                     drop_threshold: float, check_frequency: str, enabled: bool):
    with _lock, _conn() as db:
        db.execute(
            "INSERT INTO alert_configs "
            "(channel_id, email, spike_threshold, drop_threshold, check_frequency, enabled, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(channel_id) DO UPDATE SET "
            "email=excluded.email, spike_threshold=excluded.spike_threshold, "
            "drop_threshold=excluded.drop_threshold, check_frequency=excluded.check_frequency, "
            "enabled=excluded.enabled, updated_at=CURRENT_TIMESTAMP",
            (channel_id, email, spike_threshold, drop_threshold, check_frequency, int(enabled))
        )


def get_alert_config(channel_id: str) -> Optional[dict]:
    with _conn() as db:
        row = db.execute(
            "SELECT * FROM alert_configs WHERE channel_id = ?", (channel_id,)
        ).fetchone()
    return dict(row) if row else None


def get_all_alert_configs() -> list[dict]:
    with _conn() as db:
        rows = db.execute("SELECT * FROM alert_configs WHERE enabled = 1").fetchall()
    return [dict(r) for r in rows]


def delete_alert_config(channel_id: str):
    with _lock, _conn() as db:
        db.execute("DELETE FROM alert_configs WHERE channel_id = ?", (channel_id,))


# ── SMTP Config ────────────────────────────────────────────────────────────

def save_smtp_config(host: str, port: int, username: str, password: str,
                     use_tls: bool, from_addr: str):
    with _lock, _conn() as db:
        db.execute(
            "INSERT INTO smtp_config (id, host, port, username, password, use_tls, from_addr, updated_at) "
            "VALUES (1, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(id) DO UPDATE SET host=excluded.host, port=excluded.port, "
            "username=excluded.username, password=excluded.password, use_tls=excluded.use_tls, "
            "from_addr=excluded.from_addr, updated_at=CURRENT_TIMESTAMP",
            (host, int(port), username, password, int(use_tls), from_addr)
        )


def get_smtp_config() -> Optional[dict]:
    with _conn() as db:
        row = db.execute("SELECT * FROM smtp_config WHERE id = 1").fetchone()
    return dict(row) if row else None


def save_webhook_url(url: str):
    with _lock, _conn() as db:
        db.execute(
            "INSERT INTO smtp_config (id, webhook_url) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET webhook_url=excluded.webhook_url",
            (url,)
        )


def get_webhook_url() -> str:
    cfg = get_smtp_config()
    return (cfg or {}).get("webhook_url", "") or ""


# ── Growth Tracker ─────────────────────────────────────────────────────────

def get_all_snapshots(channel_id: str) -> list[dict]:
    """Return all snapshots for a channel in ASC order (oldest first)."""
    with _conn() as db:
        rows = db.execute(
            "SELECT * FROM channel_snapshots WHERE channel_id = ? "
            "ORDER BY snapshot_at ASC",
            (channel_id,)
        ).fetchall()
    return [dict(r) for r in rows]


# ── Tags ───────────────────────────────────────────────────────────────────

def update_tags(channel_id: str, tags_str: str):
    with _lock, _conn() as db:
        db.execute(
            "UPDATE pinned_channels SET tags = ? WHERE channel_id = ?",
            (tags_str, channel_id)
        )


# ── New Video Alert Polling ────────────────────────────────────────────────

def update_last_video_id(channel_id: str, video_id: str):
    with _lock, _conn() as db:
        db.execute(
            "UPDATE pinned_channels SET last_video_id = ? WHERE channel_id = ?",
            (video_id, channel_id)
        )


def get_pinned_last_video_ids() -> list[dict]:
    """Return list of {channel_id, query, last_video_id, channel_name} for all pinned."""
    with _conn() as db:
        rows = db.execute(
            "SELECT channel_id, query, last_video_id, channel_name FROM pinned_channels"
        ).fetchall()
    return [dict(r) for r in rows]


# Init on import
init_db()
