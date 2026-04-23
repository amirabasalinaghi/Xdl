from __future__ import annotations

import sqlite3
from pathlib import Path


class RelayDB:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    last_seen_tweet_id TEXT,
                    monitored_user_id TEXT
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

                CREATE TABLE IF NOT EXISTS media_hashes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    repost_tweet_id TEXT NOT NULL,
                    media_hash TEXT NOT NULL UNIQUE,
                    file_path TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS media_index (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    original_tweet_id TEXT NOT NULL,
                    media_key TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(original_tweet_id, media_key)
                );
                """
            )
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(state)").fetchall()
            }
            if "monitored_user_id" not in columns:
                conn.execute("ALTER TABLE state ADD COLUMN monitored_user_id TEXT")

    def get_last_seen_tweet_id(self) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT last_seen_tweet_id FROM state WHERE id = 1").fetchone()
            return row["last_seen_tweet_id"] if row else None

    def set_last_seen_tweet_id(self, tweet_id: str | None) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE state SET last_seen_tweet_id = ? WHERE id = 1",
                (tweet_id,),
            )

    def get_monitored_user_id(self) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT monitored_user_id FROM state WHERE id = 1").fetchone()
            return row["monitored_user_id"] if row else None

    def set_monitored_user_id(self, user_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE state SET monitored_user_id = ? WHERE id = 1",
                (user_id,),
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

    def get_repost_status(self, repost_tweet_id: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT status
                FROM repost_events
                WHERE repost_tweet_id = ?
                LIMIT 1
                """,
                (repost_tweet_id,),
            ).fetchone()
        return str(row["status"]) if row and row["status"] else None

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
                (error_message[:4000], repost_tweet_id),
            )


    def media_hash_exists(self, media_hash: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM media_hashes WHERE media_hash = ? LIMIT 1",
                (media_hash,),
            ).fetchone()
        return bool(row)

    def record_media_hash(self, repost_id: str, media_hash: str, file_path: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO media_hashes (repost_tweet_id, media_hash, file_path)
                VALUES (?, ?, ?)
                """,
                (repost_id, media_hash, file_path),
            )

    def get_indexed_media_path(self, original_tweet_id: str, media_key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT file_path
                FROM media_index
                WHERE original_tweet_id = ? AND media_key = ?
                LIMIT 1
                """,
                (original_tweet_id, media_key),
            ).fetchone()
        return str(row["file_path"]) if row and row["file_path"] else None

    def upsert_media_index(
        self,
        original_tweet_id: str,
        media_key: str,
        media_type: str,
        source_url: str,
        file_path: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO media_index (original_tweet_id, media_key, media_type, source_url, file_path)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(original_tweet_id, media_key)
                DO UPDATE SET
                    media_type=excluded.media_type,
                    source_url=excluded.source_url,
                    file_path=excluded.file_path,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (original_tweet_id, media_key, media_type, source_url, file_path),
            )

    def list_stale_pending_events(self, stale_after_minutes: int) -> list[dict[str, str | None]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT repost_tweet_id, original_tweet_id, status, error_message, created_at, updated_at
                FROM repost_events
                WHERE status = 'pending'
                  AND updated_at <= datetime('now', ?)
                ORDER BY updated_at ASC
                """,
                (f"-{max(1, stale_after_minutes)} minutes",),
            ).fetchall()
        return [dict(row) for row in rows]

    def requeue_events(self, repost_ids: list[str]) -> int:
        if not repost_ids:
            return 0
        placeholders = ",".join("?" for _ in repost_ids)
        with self._connect() as conn:
            cur = conn.execute(
                f"""
                UPDATE repost_events
                SET status = 'pending', error_message = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE repost_tweet_id IN ({placeholders})
                """,
                repost_ids,
            )
        return int(cur.rowcount or 0)

    def get_overview(self) -> dict[str, int | str | None]:
        with self._connect() as conn:
            total_events = conn.execute("SELECT COUNT(1) AS c FROM repost_events").fetchone()["c"]
            sent_events = conn.execute("SELECT COUNT(1) AS c FROM repost_events WHERE status = 'sent'").fetchone()["c"]
            failed_events = conn.execute("SELECT COUNT(1) AS c FROM repost_events WHERE status = 'failed'").fetchone()["c"]
            pending_events = conn.execute("SELECT COUNT(1) AS c FROM repost_events WHERE status = 'pending'").fetchone()["c"]
            last_update_row = conn.execute("SELECT MAX(updated_at) AS m FROM repost_events").fetchone()

        return {
            "db_path": self.db_path,
            "last_seen_tweet_id": self.get_last_seen_tweet_id(),
            "total_events": int(total_events),
            "sent_events": int(sent_events),
            "failed_events": int(failed_events),
            "pending_events": int(pending_events),
            "last_update": last_update_row["m"] if last_update_row else None,
        }

    def list_events(self, limit: int = 500, status: str | None = None, text_query: str | None = None) -> list[dict[str, str | None]]:
        where_clauses = []
        params: list[object] = []

        if status:
            where_clauses.append("status = ?")
            params.append(status)

        if text_query:
            where_clauses.append("(repost_tweet_id LIKE ? OR original_tweet_id LIKE ?)")
            like = f"%{text_query}%"
            params.extend([like, like])

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT repost_tweet_id, original_tweet_id, status, error_message, created_at, updated_at
                FROM repost_events
                {where_sql}
                ORDER BY id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

        return [dict(row) for row in rows]

    def list_delivery_logs(self, limit: int = 200) -> list[dict[str, str | None]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT repost_tweet_id, telegram_message_ids, created_at
                FROM delivery_logs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_unsent_repost_ids(self, limit: int = 2000) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT repost_tweet_id
                FROM repost_events
                WHERE status IN ('pending', 'failed')
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [str(row["repost_tweet_id"]) for row in rows if row["repost_tweet_id"]]
