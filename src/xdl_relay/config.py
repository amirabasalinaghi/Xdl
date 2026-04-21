from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    x_bearer_token: str
    x_user_id: str
    telegram_bot_token: str
    telegram_chat_id: str
    poll_interval_seconds: int = 30
    db_path: str = "relay.db"
    media_dir: str = "media"
    http_timeout_seconds: int = 30
    http_retries: int = 3
    http_backoff_seconds: float = 1.0
    max_media_bytes: int = 50 * 1024 * 1024
    x_max_pages: int = 5

    @staticmethod
    def from_env() -> "Settings":
        required = {
            "X_BEARER_TOKEN": os.getenv("X_BEARER_TOKEN", ""),
            "X_USER_ID": os.getenv("X_USER_ID", ""),
            "TELEGRAM_BOT_TOKEN": os.getenv("TELEGRAM_BOT_TOKEN", ""),
            "TELEGRAM_CHAT_ID": os.getenv("TELEGRAM_CHAT_ID", ""),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

        return Settings(
            x_bearer_token=required["X_BEARER_TOKEN"],
            x_user_id=required["X_USER_ID"],
            telegram_bot_token=required["TELEGRAM_BOT_TOKEN"],
            telegram_chat_id=required["TELEGRAM_CHAT_ID"],
            poll_interval_seconds=int(os.getenv("POLL_INTERVAL_SECONDS", "30")),
            db_path=os.getenv("DB_PATH", "relay.db"),
            media_dir=os.getenv("MEDIA_DIR", "media"),
            http_timeout_seconds=int(os.getenv("HTTP_TIMEOUT_SECONDS", "30")),
            http_retries=int(os.getenv("HTTP_RETRIES", "3")),
            http_backoff_seconds=float(os.getenv("HTTP_BACKOFF_SECONDS", "1.0")),
            max_media_bytes=int(os.getenv("MAX_MEDIA_BYTES", str(50 * 1024 * 1024))),
            x_max_pages=int(os.getenv("X_MAX_PAGES", "5")),
        )
