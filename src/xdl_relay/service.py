from __future__ import annotations

import logging
import time
from pathlib import Path

from xdl_relay.config import Settings
from xdl_relay.db import RelayDB
from xdl_relay.models import RepostEvent
from xdl_relay.storage import download_file
from xdl_relay.telegram_client import TelegramClient
from xdl_relay.x_client import XClient

logger = logging.getLogger(__name__)


class RelayService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.db = RelayDB(settings.db_path)
        self.x_client = XClient(
            settings.x_bearer_token,
            timeout=settings.http_timeout_seconds,
            retries=settings.http_retries,
            backoff_seconds=settings.http_backoff_seconds,
            max_pages=settings.x_max_pages,
        )
        self.telegram_client = TelegramClient(settings.telegram_bot_token)
        self.media_dir = Path(settings.media_dir)
        self.media_dir.mkdir(parents=True, exist_ok=True)

    def process_once(self) -> int:
        since_id = self.db.get_last_seen_tweet_id()
        reposts = self.x_client.get_new_reposts(self.settings.x_user_id, since_id)
        if not reposts:
            return 0

        processed = 0
        for event in reposts:
            created, succeeded = self._process_event(event)
            if created and succeeded:
                processed += 1
                self.db.set_last_seen_tweet_id(event.repost_tweet_id)
            elif not created:
                # event already exists, safe to advance cursor
                self.db.set_last_seen_tweet_id(event.repost_tweet_id)
            else:
                logger.warning("Skipping last_seen update due to failed repost %s", event.repost_tweet_id)

        return processed

    def run_forever(self) -> None:
        logger.info("Starting relay with poll interval=%ss", self.settings.poll_interval_seconds)
        while True:
            try:
                count = self.process_once()
                if count:
                    logger.info("Processed %s repost event(s)", count)
            except Exception as exc:
                logger.exception("Polling cycle failed: %s", exc)
            time.sleep(self.settings.poll_interval_seconds)

    def _process_event(self, event: RepostEvent) -> tuple[bool, bool]:
        created = self.db.create_repost_event(event.repost_tweet_id, event.original_tweet_id)
        if not created:
            return False, False

        try:
            files: list[Path] = []
            for idx, media in enumerate(event.media):
                suffix = ".mp4" if media.media_type != "photo" else ".jpg"
                path = self.media_dir / event.repost_tweet_id / f"{idx}_{media.media_key}{suffix}"
                files.append(
                    download_file(
                        media.url,
                        path,
                        timeout=self.settings.http_timeout_seconds,
                        max_bytes=self.settings.max_media_bytes,
                    )
                )

            message_ids = self.telegram_client.send_media(
                self.settings.telegram_chat_id,
                files,
                caption=self._build_caption(event) if self.settings.telegram_include_caption else None,
            )
            self.db.mark_sent(event.repost_tweet_id, ",".join(str(mid) for mid in message_ids))
            return True, True
        except Exception as exc:
            self.db.mark_failed(event.repost_tweet_id, str(exc))
            if self.settings.telegram_failure_alerts:
                self._notify_failure(event.repost_tweet_id, exc)
            logger.exception("Failed processing repost %s", event.repost_tweet_id)
            return True, False

    def _build_caption(self, event: RepostEvent) -> str:
        title = event.original_text or event.repost_text or "Repost media forwarded"
        safe_title = " ".join(title.split())
        if len(safe_title) > 280:
            safe_title = f"{safe_title[:277]}..."
        return (
            f"{safe_title}\n\n"
            f"Original: https://x.com/i/web/status/{event.original_tweet_id}\n"
            f"Repost: https://x.com/i/web/status/{event.repost_tweet_id}"
        )

    def _notify_failure(self, repost_tweet_id: str, error: Exception) -> None:
        try:
            self.telegram_client.send_message(
                self.settings.telegram_chat_id,
                f"⚠️ Relay failed for repost {repost_tweet_id}: {str(error)[:350]}",
            )
        except Exception:
            logger.exception("Failed to send Telegram failure alert for repost %s", repost_tweet_id)
