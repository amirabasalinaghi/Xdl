from __future__ import annotations

import sqlite3
from pathlib import Path


class RelayDB:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    last_seen_tweet_id TEXT
                );

                INSERT OR IGNORE INTO state (id, last_seen_tweet_id) VALUES (1, NULL);

                CREATE TABLE IF NOT EXISTS repost_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    repost_tweet_id TEXT UNIQUE NOT NULL,
                    original_tweet_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    error_message TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS delivery_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    repost_tweet_id TEXT NOT NULL,
                    telegram_message_ids TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

    def get_last_seen_tweet_id(self) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT last_seen_tweet_id FROM state WHERE id = 1").fetchone()
            return row["last_seen_tweet_id"] if row else None

    def set_last_seen_tweet_id(self, tweet_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE state SET last_seen_tweet_id = ? WHERE id = 1",
                (tweet_id,),
            )

    def create_repost_event(self, repost_tweet_id: str, original_tweet_id: str) -> bool:
        with self._connect() as conn:
            try:
                conn.execute(
                    "INSERT INTO repost_events (repost_tweet_id, original_tweet_id) VALUES (?, ?)",
                    (repost_tweet_id, original_tweet_id),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def mark_sent(self, repost_tweet_id: str, telegram_message_ids: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE repost_events SET status = 'sent', updated_at = CURRENT_TIMESTAMP WHERE repost_tweet_id = ?",
                (repost_tweet_id,),
            )
            conn.execute(
                "INSERT INTO delivery_logs (repost_tweet_id, telegram_message_ids) VALUES (?, ?)",
                (repost_tweet_id, telegram_message_ids),
            )

    def mark_failed(self, repost_tweet_id: str, error_message: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE repost_events SET status = 'failed', error_message = ?, updated_at = CURRENT_TIMESTAMP WHERE repost_tweet_id = ?",
                (error_message[:1000], repost_tweet_id),
            )
