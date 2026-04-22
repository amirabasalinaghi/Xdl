from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from xdl_relay.config import Settings
from xdl_relay.models import MediaItem, RepostEvent
from xdl_relay.service import RelayService


class _FakeXClient:
    def __init__(self, events: list[RepostEvent]) -> None:
        self.events = events

    def get_new_reposts(self, user_id: str, since_id: str | None = None) -> list[RepostEvent]:
        return self.events


class _CursorAwareFakeXClient:
    def __init__(self, responses: dict[str | None, list[RepostEvent]]) -> None:
        self.responses = responses
        self.calls: list[str | None] = []

    def get_new_reposts(self, user_id: str, since_id: str | None = None) -> list[RepostEvent]:
        self.calls.append(since_id)
        return self.responses.get(since_id, [])


class _FailingTelegramClient:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send_media(self, chat_id: str, files: list[Path], caption: str | None = None) -> list[int]:
        raise RuntimeError("telegram down")

    def send_message(self, chat_id: str, text: str) -> int:
        self.messages.append(text)
        return 1


class _SuccessfulTelegramClient:
    def send_media(self, chat_id: str, files: list[Path], caption: str | None = None) -> list[int]:
        return [123]


class TestServiceBehavior(unittest.TestCase):
    def test_failed_event_advances_last_seen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(
                x_user_id="user",
                x_bearer_token="bearer",
                telegram_bot_token="tg",
                telegram_chat_id="chat",
                db_path=str(Path(tmp) / "relay.db"),
                media_dir=str(Path(tmp) / "media"),
                max_media_bytes=1024,
            )
            service = RelayService(settings)
            service.x_client = _FakeXClient(
                [
                    RepostEvent(
                        repost_tweet_id="200",
                        original_tweet_id="100",
                        original_author_id="abc",
                        repost_text="RT: something cool",
                        original_text="something cool",
                        media=[MediaItem(media_key="m1", media_type="photo", url="https://example.com/a.jpg")],
                    )
                ]
            )
            telegram = _FailingTelegramClient()
            service.telegram_client = telegram

            processed = service.process_once()
            self.assertEqual(processed, 0)
            self.assertEqual(service.db.get_last_seen_tweet_id(), "200")
            self.assertTrue(telegram.messages)

    def test_caption_contains_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(
                x_user_id="user",
                x_bearer_token="bearer",
                telegram_bot_token="tg",
                telegram_chat_id="chat",
                db_path=str(Path(tmp) / "relay.db"),
                media_dir=str(Path(tmp) / "media"),
            )
            service = RelayService(settings)
            caption = service._build_caption(
                RepostEvent(
                    repost_tweet_id="200",
                    original_tweet_id="100",
                    original_author_id="abc",
                    repost_text="",
                    original_text="hello world",
                    media=[],
                )
            )
            self.assertIn("hello world", caption)
            self.assertIn("https://x.com/i/web/status/100", caption)
            self.assertIn("https://x.com/i/web/status/200", caption)

    def test_filter_media_by_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(
                x_user_id="user",
                x_bearer_token="bearer",
                telegram_bot_token="tg",
                telegram_chat_id="chat",
                db_path=str(Path(tmp) / "relay.db"),
                media_dir=str(Path(tmp) / "media"),
                media_download_mode="pic",
            )
            service = RelayService(settings)
            media = [
                MediaItem(media_key="m1", media_type="photo", url="https://example.com/a.jpg"),
                MediaItem(media_key="m2", media_type="video", url="https://example.com/v.mp4"),
            ]
            self.assertEqual([m.media_key for m in service._filter_media_by_mode(media)], ["m1"])

            service.update_settings(
                Settings(
                    x_user_id="user",
                    x_bearer_token="bearer",
                    telegram_bot_token="tg",
                    telegram_chat_id="chat",
                    db_path=str(Path(tmp) / "relay.db"),
                    media_dir=str(Path(tmp) / "media"),
                    media_download_mode="video",
                )
            )
            self.assertEqual([m.media_key for m in service._filter_media_by_mode(media)], ["m2"])

    def test_force_refresh_retries_unsent_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(
                x_user_id="user",
                x_bearer_token="bearer",
                telegram_bot_token="tg",
                telegram_chat_id="chat",
                db_path=str(Path(tmp) / "relay.db"),
                media_dir=str(Path(tmp) / "media"),
            )
            service = RelayService(settings)
            event = RepostEvent(
                repost_tweet_id="200",
                original_tweet_id="100",
                original_author_id="abc",
                repost_text="RT: something cool",
                original_text="something cool",
                media=[MediaItem(media_key="m1", media_type="photo", url="https://example.com/a.jpg")],
            )
            service.db.create_repost_event("200", "100")
            service.db.mark_failed("200", "temporary failure")
            service.x_client = _FakeXClient([event])
            service.telegram_client = _SuccessfulTelegramClient()

            with mock.patch("xdl_relay.service.download_file", return_value=Path(tmp) / "a.jpg"):
                result = service.force_refresh_and_retry_unsent()

            self.assertEqual(result["fetched"], 1)
            self.assertEqual(result["retried"], 1)
            self.assertEqual(result["retried_success"], 1)
            self.assertEqual(result["new_processed"], 0)
            status = service.db.list_events(limit=10, status=None, text_query="200")[0]["status"]
            self.assertEqual(status, "sent")

    def test_process_once_runs_catch_up_scan_when_since_id_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(
                x_user_id="user",
                x_bearer_token="bearer",
                telegram_bot_token="tg",
                telegram_chat_id="chat",
                db_path=str(Path(tmp) / "relay.db"),
                media_dir=str(Path(tmp) / "media"),
            )
            service = RelayService(settings)
            service.db.set_last_seen_tweet_id("200")
            event = RepostEvent(
                repost_tweet_id="205",
                original_tweet_id="100",
                original_author_id="abc",
                repost_text="RT: something cool",
                original_text="something cool",
                media=[MediaItem(media_key="m1", media_type="photo", url="https://example.com/a.jpg")],
            )
            fake_x_client = _CursorAwareFakeXClient({"200": [], None: [event]})
            service.x_client = fake_x_client
            service.telegram_client = _SuccessfulTelegramClient()

            with mock.patch("xdl_relay.service.download_file", return_value=Path(tmp) / "a.jpg"):
                processed = service.process_once()

            self.assertEqual(processed, 1)
            self.assertEqual(fake_x_client.calls, ["200", None])
            self.assertEqual(service.db.get_last_seen_tweet_id(), "205")


if __name__ == "__main__":
    unittest.main()
