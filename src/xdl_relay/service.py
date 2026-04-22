from __future__ import annotations

import logging
import time
from pathlib import Path

from xdl_relay.config import Settings
from xdl_relay.db import RelayDB
from xdl_relay.enhancements import build_repost_permalink, split_caption_chunks
from xdl_relay.models import MediaItem, RepostEvent
from xdl_relay.storage import download_file
from xdl_relay.telegram_client import TelegramClient
from xdl_relay.x_client import XClient

logger = logging.getLogger(__name__)


class RelayService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.db = RelayDB(settings.db_path)
        self.x_client = XClient(
            timeout=settings.http_timeout_seconds,
            retries=settings.http_retries,
            backoff_seconds=settings.http_backoff_seconds,
            max_pages=settings.x_max_pages,
            bearer_token=settings.x_bearer_token,
        )
        self.telegram_client = TelegramClient(settings.telegram_bot_token)
        self.media_dir = Path(settings.media_dir)
        self.media_dir.mkdir(parents=True, exist_ok=True)

    def update_settings(self, settings: Settings) -> None:
        self.settings = settings
        self.x_client = XClient(
            timeout=settings.http_timeout_seconds,
            retries=settings.http_retries,
            backoff_seconds=settings.http_backoff_seconds,
            max_pages=settings.x_max_pages,
            bearer_token=settings.x_bearer_token,
        )
        self.telegram_client = TelegramClient(settings.telegram_bot_token)
        self.media_dir = Path(settings.media_dir)
        self.media_dir.mkdir(parents=True, exist_ok=True)

    def process_once(self) -> int:
        since_id = self.db.get_last_seen_tweet_id()
        reposts = self.x_client.get_new_reposts(self.settings.x_user_id, since_id)
        if since_id:
            reposts = self._filter_reposts_newer_than_cursor(reposts, since_id)
        if not reposts and since_id:
            logger.info(
                "No reposts returned for since_id=%s; running catch-up scan without cursor to avoid missing recent reposts.",
                since_id,
            )
            recent_reposts = self.x_client.get_new_reposts(self.settings.x_user_id, since_id=None)
            reposts = self._filter_reposts_newer_than_cursor(recent_reposts, since_id)
        if not reposts:
            return 0

        processed = 0
        for event in reposts:
            logger.info(
                "Processing repost=%s original=%s media_count=%s",
                event.repost_tweet_id,
                event.original_tweet_id,
                len(event.media),
            )
            created, succeeded = self._process_event(event)
            if succeeded:
                processed += 1
            if not succeeded:
                logger.warning("Repost %s failed delivery; cursor will still advance", event.repost_tweet_id)
            # Always advance cursor for fetched events so one failing repost
            # does not block discovering newer reposts.
            self.db.set_last_seen_tweet_id(event.repost_tweet_id)

        return processed

    def _filter_reposts_newer_than_cursor(self, reposts: list[RepostEvent], since_id: str) -> list[RepostEvent]:
        def _as_int(value: str) -> int | None:
            return int(value) if value.isdigit() else None

        cursor_id = _as_int(since_id)
        if cursor_id is None:
            return reposts

        filtered = []
        for event in reposts:
            event_id = _as_int(event.repost_tweet_id)
            if event_id is None or event_id > cursor_id:
                filtered.append(event)
        return sorted(filtered, key=lambda event: _as_int(event.repost_tweet_id) or 0)

    def force_refresh_and_retry_unsent(self) -> dict[str, int]:
        unsent_repost_ids = set(self.db.list_unsent_repost_ids())
        reposts = self.x_client.get_new_reposts(self.settings.x_user_id, since_id=None)
        if not reposts:
            return {"fetched": 0, "retried": 0, "retried_success": 0, "new_processed": 0}

        retried = 0
        retried_success = 0
        new_processed = 0

        for event in reposts:
            if event.repost_tweet_id in unsent_repost_ids:
                retried += 1
                if self._deliver_event(event):
                    retried_success += 1
                continue

            created, succeeded = self._process_event(event)
            if created and succeeded:
                new_processed += 1

        return {
            "fetched": len(reposts),
            "retried": retried,
            "retried_success": retried_success,
            "new_processed": new_processed,
        }

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
        return True, self._deliver_event(event)

    def _deliver_event(self, event: RepostEvent) -> bool:
        try:
            selected_media = self._filter_media_by_mode(event.media)
            logger.info(
                "Selected %s/%s media items for repost=%s mode=%s",
                len(selected_media),
                len(event.media),
                event.repost_tweet_id,
                self.settings.media_download_mode,
            )
            if not selected_media:
                self.db.mark_failed(
                    event.repost_tweet_id,
                    f"No media matched download mode '{self.settings.media_download_mode}'",
                )
                return False

            files: list[Path] = []
            for idx, media in enumerate(selected_media):
                suffix = ".mp4" if media.media_type != "photo" else ".jpg"
                path = self.media_dir / event.repost_tweet_id / f"{idx}_{media.media_key}{suffix}"
                logger.info(
                    "Downloading media repost=%s idx=%s key=%s type=%s url=%s path=%s",
                    event.repost_tweet_id,
                    idx,
                    media.media_key,
                    media.media_type,
                    media.url,
                    path,
                )
                files.append(
                    download_file(
                        media.url,
                        path,
                        timeout=self.settings.http_timeout_seconds,
                        max_bytes=self.settings.max_media_bytes if self.settings.max_media_bytes > 0 else None,
                    )
                )
            logger.info("Sending %s files to Telegram for repost=%s", len(files), event.repost_tweet_id)

            message_ids = self.telegram_client.send_media(
                self.settings.telegram_chat_id,
                files,
                caption=self._build_caption(event) if self.settings.telegram_include_caption else None,
            )
            self.db.mark_sent(event.repost_tweet_id, ",".join(str(mid) for mid in message_ids))
            return True
        except Exception as exc:
            self.db.mark_failed(event.repost_tweet_id, str(exc))
            if self.settings.telegram_failure_alerts:
                self._notify_failure(event.repost_tweet_id, exc)
            logger.exception("Failed processing repost %s", event.repost_tweet_id)
            return False

    def _filter_media_by_mode(self, media_items: list[MediaItem]) -> list[MediaItem]:
        mode = (self.settings.media_download_mode or "both").lower()
        if mode == "pic":
            return [item for item in media_items if item.media_type == "photo"]
        if mode == "video":
            return [item for item in media_items if item.media_type != "photo"]
        return media_items

    def _build_caption(self, event: RepostEvent) -> str:
        title = event.original_text or event.repost_text or "Repost media forwarded"
        safe_title = " ".join(title.split())
        if len(safe_title) > 900:
            safe_title = f"{safe_title[:897]}..."
        caption = (
            f"{safe_title}\n\n"
            f"Original: {build_repost_permalink(event.original_tweet_id)}\n"
            f"Repost: {build_repost_permalink(event.repost_tweet_id)}"
        )
        return split_caption_chunks(caption, max_len=1024)[0]

    def _notify_failure(self, repost_tweet_id: str, error: Exception) -> None:
        try:
            self.telegram_client.send_message(
                self.settings.telegram_chat_id,
                f"⚠️ Relay failed for repost {repost_tweet_id}: {str(error)[:1000]}",
            )
        except Exception:
            logger.exception("Failed to send Telegram failure alert for repost %s", repost_tweet_id)
