from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    x_user_id: str
    x_bearer_token: str
    telegram_bot_token: str
    telegram_chat_id: str
    poll_interval_seconds: int = 30
    db_path: str = "relay.db"
    media_dir: str = "media"
    http_timeout_seconds: int = 30
    http_retries: int = 3
    http_backoff_seconds: float = 1.0
    max_media_bytes: int = 0
    x_max_pages: int = 32
    media_download_mode: str = "both"
    telegram_include_caption: bool = True
    telegram_failure_alerts: bool = True

    @staticmethod
    def from_env() -> "Settings":
        required = {
            "X_USER_ID": os.getenv("X_USER_ID", ""),
            "X_BEARER_TOKEN": os.getenv("X_BEARER_TOKEN", ""),
            "TELEGRAM_BOT_TOKEN": os.getenv("TELEGRAM_BOT_TOKEN", ""),
            "TELEGRAM_CHAT_ID": os.getenv("TELEGRAM_CHAT_ID", ""),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

        return Settings(
            x_user_id=required["X_USER_ID"],
            x_bearer_token=required["X_BEARER_TOKEN"],
            telegram_bot_token=required["TELEGRAM_BOT_TOKEN"],
            telegram_chat_id=required["TELEGRAM_CHAT_ID"],
            poll_interval_seconds=int(os.getenv("POLL_INTERVAL_SECONDS", "30")),
            db_path=os.getenv("DB_PATH", "relay.db"),
            media_dir=os.getenv("MEDIA_DIR", "media"),
            http_timeout_seconds=int(os.getenv("HTTP_TIMEOUT_SECONDS", "30")),
            http_retries=int(os.getenv("HTTP_RETRIES", "3")),
            http_backoff_seconds=float(os.getenv("HTTP_BACKOFF_SECONDS", "1.0")),
            max_media_bytes=int(os.getenv("MAX_MEDIA_BYTES", "0")),
            x_max_pages=int(os.getenv("X_MAX_PAGES", "32")),
            media_download_mode=os.getenv("MEDIA_DOWNLOAD_MODE", "both").lower(),
            telegram_include_caption=os.getenv("TELEGRAM_INCLUDE_CAPTION", "1").lower()
            in {"1", "true", "yes", "on"},
            telegram_failure_alerts=os.getenv("TELEGRAM_FAILURE_ALERTS", "1").lower()
            in {"1", "true", "yes", "on"},
        )

    def to_env_dict(self) -> dict[str, str]:
        return {
            "X_USER_ID": self.x_user_id,
            "X_BEARER_TOKEN": self.x_bearer_token,
            "TELEGRAM_BOT_TOKEN": self.telegram_bot_token,
            "TELEGRAM_CHAT_ID": self.telegram_chat_id,
            "POLL_INTERVAL_SECONDS": str(self.poll_interval_seconds),
            "DB_PATH": self.db_path,
            "MEDIA_DIR": self.media_dir,
            "HTTP_TIMEOUT_SECONDS": str(self.http_timeout_seconds),
            "HTTP_RETRIES": str(self.http_retries),
            "HTTP_BACKOFF_SECONDS": str(self.http_backoff_seconds),
            "MAX_MEDIA_BYTES": str(self.max_media_bytes),
            "X_MAX_PAGES": str(self.x_max_pages),
            "MEDIA_DOWNLOAD_MODE": self.media_download_mode,
            "TELEGRAM_INCLUDE_CAPTION": "1" if self.telegram_include_caption else "0",
            "TELEGRAM_FAILURE_ALERTS": "1" if self.telegram_failure_alerts else "0",
        }
