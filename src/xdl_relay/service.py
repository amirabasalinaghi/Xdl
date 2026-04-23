from __future__ import annotations

import logging
import threading
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
MAX_AUTO_FAILED_RETRIES = 5
RUN_TIMEOUT_SECONDS = 5 * 60


class RelayService:
    def __init__(self, settings: Settings) -> None:
        self._process_lock = threading.Lock()
        self._run_state_lock = threading.Lock()
        self._active_run_started_at: float | None = None
        self.settings = settings
        self.db = RelayDB(settings.db_path)
        self.x_client = XClient(
            timeout=settings.http_timeout_seconds,
            retries=settings.http_retries,
            backoff_seconds=settings.http_backoff_seconds,
            max_pages=settings.x_max_pages,
            page_size=settings.x_page_size,
            bearer_token=settings.x_bearer_token,
        )
        self.telegram_client = TelegramClient(settings.telegram_bot_token)
        self.media_dir = Path(settings.media_dir)
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self._last_profile_scan_stats: dict[str, int] = {
            "total_profile_posts_seen": 0,
            "total_reposts_seen": 0,
            "total_replies_seen": 0,
            "total_quotes_seen": 0,
            "total_original_posts_seen": 0,
            "total_other_reference_posts_seen": 0,
        }

    def update_settings(self, settings: Settings) -> None:
        with self._process_lock:
            self.settings = settings
            self.x_client = XClient(
                timeout=settings.http_timeout_seconds,
                retries=settings.http_retries,
                backoff_seconds=settings.http_backoff_seconds,
                max_pages=settings.x_max_pages,
                page_size=settings.x_page_size,
                bearer_token=settings.x_bearer_token,
            )
            self.telegram_client = TelegramClient(settings.telegram_bot_token)
            self.media_dir = Path(settings.media_dir)
            self.media_dir.mkdir(parents=True, exist_ok=True)

    def process_once(self) -> int:
        stats = self._run_poll_cycle(log_prefix="Polling")
        return stats["processed"]

    def process_once_with_stats(self) -> dict[str, int]:
        return self._run_poll_cycle(log_prefix="Manual")

    def index_full_profile_with_stats(self) -> dict[str, int]:
        return self._run_poll_cycle(log_prefix="Full profile index", use_checkpoint=False)

    def poll_with_stats(self) -> dict[str, int]:
        return self._run_poll_cycle(log_prefix="Polling")

    def overview_with_profile_stats(self) -> dict[str, int | str | None]:
        return self.db.get_overview()

    def _run_poll_cycle(self, log_prefix: str, use_checkpoint: bool = True) -> dict[str, int]:
        if not self._try_start_run(log_prefix=log_prefix):
            return self._empty_poll_result()
        try:
            return self._poll_with_stats(log_prefix=log_prefix, use_checkpoint=use_checkpoint)
        finally:
            self._finish_run()

    def _try_start_run(self, log_prefix: str) -> bool:
        now = time.monotonic()
        with self._run_state_lock:
            if self._active_run_started_at is None:
                self._active_run_started_at = now
                return True
            active_for = now - self._active_run_started_at
            if active_for < RUN_TIMEOUT_SECONDS:
                logger.info(
                    "%s run skipped because another run is still active (active_for=%.1fs, timeout=%ss)",
                    log_prefix,
                    active_for,
                    RUN_TIMEOUT_SECONDS,
                )
                return False
            logger.warning(
                "Previous run exceeded timeout (%ss). Starting a new run.",
                RUN_TIMEOUT_SECONDS,
            )
            self._active_run_started_at = now
            return True

    def _finish_run(self) -> None:
        with self._run_state_lock:
            self._active_run_started_at = None

    def _poll_with_stats(self, log_prefix: str, use_checkpoint: bool = True) -> dict[str, int]:
        self._sync_checkpoint_scope_with_current_user()
        since_id = self.db.get_last_seen_tweet_id() if use_checkpoint else None
        if hasattr(self.x_client, "get_new_reposts_with_stats"):
            reposts, profile_stats = self.x_client.get_new_reposts_with_stats(self.settings.x_user_id, since_id=since_id)
        else:
            reposts = self.x_client.get_new_reposts(self.settings.x_user_id, since_id=since_id)
            profile_stats = {
                "total_profile_posts_seen": len(reposts),
                "total_reposts_seen": 0,
                "total_replies_seen": 0,
                "total_quotes_seen": 0,
                "total_original_posts_seen": 0,
                "total_other_reference_posts_seen": 0,
            }
        self._last_profile_scan_stats = profile_stats
        self.db.add_profile_scan_totals(profile_stats)
        latest_profile_tweet_id = getattr(self.x_client, "latest_profile_tweet_id", None)
        reposts = [event for event in reposts if self._event_has_relayable_media(event)]
        pic_count, video_count = self._count_media_types(reposts)
        if not reposts:
            if latest_profile_tweet_id:
                self.db.set_last_seen_tweet_id(str(latest_profile_tweet_id))
            return self._empty_poll_result(profile_stats=profile_stats)

        new_count = 0
        processed = 0
        for event in reposts:
            logger.info(
                "%s processing repost=%s original=%s media_count=%s",
                log_prefix,
                event.repost_tweet_id,
                event.original_tweet_id,
                len(event.media),
            )
            created, succeeded = self._process_event(event)
            if created:
                new_count += 1
            if succeeded:
                processed += 1
            self.db.set_last_seen_tweet_id(event.repost_tweet_id)

        if latest_profile_tweet_id:
            self.db.set_last_seen_tweet_id(str(latest_profile_tweet_id))

        return {
            "fetched": len(reposts),
            "pics": pic_count,
            "videos": video_count,
            "new": new_count,
            "processed": processed,
            **profile_stats,
        }

    def _empty_poll_result(self, profile_stats: dict[str, int] | None = None) -> dict[str, int]:
        return {
            "fetched": 0,
            "pics": 0,
            "videos": 0,
            "new": 0,
            "processed": 0,
            **(profile_stats or self._last_profile_scan_stats),
        }

    def _sync_checkpoint_scope_with_current_user(self) -> None:
        current_user = self.settings.x_user_id.strip()
        stored_user = self.db.get_monitored_user_id()
        if not current_user:
            return

        if stored_user is None:
            # One-time migration for legacy DBs where last_seen_tweet_id had no user scope.
            if self.db.get_last_seen_tweet_id():
                logger.warning(
                    "Resetting legacy checkpoint because monitored user scope was not recorded. user=%s",
                    current_user,
                )
                self.db.set_last_seen_tweet_id(None)
            self.db.set_monitored_user_id(current_user)
            return

        if stored_user != current_user:
            logger.warning(
                "Monitored user changed from %s to %s. Resetting checkpoint and runtime history.",
                stored_user,
                current_user,
            )
            self.db.reset_runtime_history()
            self.db.set_last_seen_tweet_id(None)
            self.db.set_monitored_user_id(current_user)

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
            status = (self.db.get_repost_status(event.repost_tweet_id) or "").lower()
            if status != "failed":
                return False, False
            failure_count = self.db.get_repost_failure_count(event.repost_tweet_id)
            if failure_count >= MAX_AUTO_FAILED_RETRIES:
                logger.info(
                    "Skipping repost=%s after %s failed attempt(s); waiting for manual retry",
                    event.repost_tweet_id,
                    failure_count,
                )
                return False, False
            logger.info(
                "Retrying failed repost=%s attempt=%s/%s",
                event.repost_tweet_id,
                failure_count + 1,
                MAX_AUTO_FAILED_RETRIES,
            )
            return False, self._deliver_event(event)
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
                path = self.media_dir / event.original_tweet_id / f"{media.media_key}{suffix}"
                cached = self._resolve_cached_media_path(event.original_tweet_id, media.media_key, path)
                if cached is not None:
                    logger.info(
                        "Reusing cached media original=%s key=%s path=%s",
                        event.original_tweet_id,
                        media.media_key,
                        cached,
                    )
                    files.append(cached)
                    continue
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
                        retries=self.settings.http_retries,
                        backoff_seconds=self.settings.http_backoff_seconds,
                    )
                )
                self.db.upsert_media_index(
                    original_tweet_id=event.original_tweet_id,
                    media_key=media.media_key,
                    media_type=media.media_type,
                    source_url=media.url,
                    file_path=str(files[-1]),
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
            failure_count = self.db.mark_failed(event.repost_tweet_id, str(exc))
            should_notify = failure_count == 1 and not self.db.was_failure_notified(event.repost_tweet_id)
            if self.settings.telegram_failure_alerts and should_notify:
                self._notify_failure(event.repost_tweet_id, exc)
                self.db.mark_failure_notified(event.repost_tweet_id)
            logger.exception("Failed processing repost %s", event.repost_tweet_id)
            return False

    def retry_failed_events(self) -> int:
        return self.db.reset_failed_attempts()

    def _resolve_cached_media_path(self, original_tweet_id: str, media_key: str, fallback_path: Path) -> Path | None:
        indexed_path = self.db.get_indexed_media_path(original_tweet_id, media_key)
        if indexed_path:
            indexed = Path(indexed_path)
            if indexed.exists():
                return indexed
        if fallback_path.exists():
            self.db.upsert_media_index(
                original_tweet_id=original_tweet_id,
                media_key=media_key,
                media_type="unknown",
                source_url="",
                file_path=str(fallback_path),
            )
            return fallback_path
        return None

    def _filter_media_by_mode(self, media_items: list[MediaItem]) -> list[MediaItem]:
        media_items = [item for item in media_items if self._is_supported_media_type(item.media_type)]
        mode = (self.settings.media_download_mode or "both").lower()
        if mode == "pic":
            return [item for item in media_items if item.media_type == "photo"]
        if mode == "video":
            return [item for item in media_items if item.media_type != "photo"]
        return media_items

    def _event_has_relayable_media(self, event: RepostEvent) -> bool:
        return any(self._is_supported_media_type(media.media_type) for media in event.media)

    @staticmethod
    def _is_supported_media_type(media_type: str) -> bool:
        return media_type in {"photo", "video", "animated_gif"}

    def _count_media_types(self, reposts: list[RepostEvent]) -> tuple[int, int]:
        pics = 0
        videos = 0
        for event in reposts:
            for media in event.media:
                if media.media_type == "photo":
                    pics += 1
                else:
                    videos += 1
        return pics, videos

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
