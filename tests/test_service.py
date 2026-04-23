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

    def test_process_once_with_stats_reports_counts(self) -> None:
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
            service.x_client = _FakeXClient(
                [
                    RepostEvent(
                        repost_tweet_id="200",
                        original_tweet_id="100",
                        original_author_id="abc",
                        repost_text="RT 1",
                        original_text="one",
                        media=[MediaItem(media_key="m1", media_type="photo", url="https://example.com/a.jpg")],
                    ),
                    RepostEvent(
                        repost_tweet_id="201",
                        original_tweet_id="101",
                        original_author_id="def",
                        repost_text="RT 2",
                        original_text="two",
                        media=[MediaItem(media_key="m2", media_type="video", url="https://example.com/v.mp4")],
                    ),
                ]
            )
            service.telegram_client = _SuccessfulTelegramClient()

            with mock.patch("xdl_relay.service.download_file", return_value=Path(tmp) / "a.jpg"):
                result = service.process_once_with_stats()

            self.assertEqual(result["fetched"], 2)
            self.assertEqual(result["pics"], 1)
            self.assertEqual(result["videos"], 1)
            self.assertEqual(result["new"], 2)
            self.assertEqual(result["processed"], 2)

    def test_index_full_profile_with_stats_processes_only_new_events(self) -> None:
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
            existing = RepostEvent(
                repost_tweet_id="200",
                original_tweet_id="100",
                original_author_id="abc",
                repost_text="RT existing",
                original_text="existing",
                media=[MediaItem(media_key="m1", media_type="photo", url="https://example.com/a.jpg")],
            )
            new_event = RepostEvent(
                repost_tweet_id="201",
                original_tweet_id="101",
                original_author_id="def",
                repost_text="RT new",
                original_text="new",
                media=[MediaItem(media_key="m2", media_type="video", url="https://example.com/v.mp4")],
            )
            service.db.create_repost_event("200", "100")
            service.x_client = _FakeXClient([existing, new_event])
            service.telegram_client = _SuccessfulTelegramClient()

            with mock.patch("xdl_relay.service.download_file", return_value=Path(tmp) / "media.bin"):
                result = service.index_full_profile_with_stats()

            self.assertEqual(result["fetched"], 2)
            self.assertEqual(result["pics"], 1)
            self.assertEqual(result["videos"], 1)
            self.assertEqual(result["new"], 1)
            self.assertEqual(result["processed"], 1)

    def test_delivery_uses_http_retry_settings_for_downloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(
                x_user_id="user",
                x_bearer_token="bearer",
                telegram_bot_token="tg",
                telegram_chat_id="chat",
                db_path=str(Path(tmp) / "relay.db"),
                media_dir=str(Path(tmp) / "media"),
                http_timeout_seconds=17,
                http_retries=4,
                http_backoff_seconds=0.5,
            )
            service = RelayService(settings)
            service.x_client = _FakeXClient(
                [
                    RepostEvent(
                        repost_tweet_id="200",
                        original_tweet_id="100",
                        original_author_id="abc",
                        repost_text="RT 1",
                        original_text="one",
                        media=[MediaItem(media_key="m1", media_type="photo", url="https://example.com/a.jpg")],
                    )
                ]
            )
            service.telegram_client = _SuccessfulTelegramClient()

            with mock.patch("xdl_relay.service.download_file", return_value=Path(tmp) / "a.jpg") as dl_mock:
                result = service.process_once_with_stats()

            self.assertEqual(result["processed"], 1)
            kwargs = dl_mock.call_args.kwargs
            self.assertEqual(kwargs["timeout"], 17)
            self.assertEqual(kwargs["retries"], 4)
            self.assertEqual(kwargs["backoff_seconds"], 0.5)

    def test_process_once_full_scan_ignores_existing_db_events(self) -> None:
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
            stale_event = RepostEvent(
                repost_tweet_id="450",
                original_tweet_id="100",
                original_author_id="abc",
                repost_text="old repost",
                original_text="old post",
                media=[MediaItem(media_key="m1", media_type="photo", url="https://example.com/a.jpg")],
            )
            service.db.create_repost_event("450", "100")
            service.x_client = _FakeXClient([stale_event])
            service.telegram_client = _SuccessfulTelegramClient()

            with mock.patch("xdl_relay.service.download_file", return_value=Path(tmp) / "a.jpg"):
                processed = service.process_once()

            self.assertEqual(processed, 0)
            self.assertEqual(service.db.get_last_seen_tweet_id(), "450")

    def test_process_once_reuses_indexed_media_for_same_original(self) -> None:
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
            media_item = MediaItem(media_key="m1", media_type="photo", url="https://example.com/a.jpg")
            service.x_client = _FakeXClient(
                [
                    RepostEvent(
                        repost_tweet_id="200",
                        original_tweet_id="100",
                        original_author_id="abc",
                        repost_text="first repost",
                        original_text="orig",
                        media=[media_item],
                    ),
                    RepostEvent(
                        repost_tweet_id="201",
                        original_tweet_id="100",
                        original_author_id="abc",
                        repost_text="second repost",
                        original_text="orig",
                        media=[media_item],
                    ),
                ]
            )
            service.telegram_client = _SuccessfulTelegramClient()

            def _download(url: str, destination: Path, **kwargs: object) -> Path:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"img")
                return destination

            with mock.patch("xdl_relay.service.download_file", side_effect=_download) as dl_mock:
                processed = service.process_once()

            self.assertEqual(processed, 2)
            self.assertEqual(dl_mock.call_count, 1)

    def test_process_once_retries_failed_event(self) -> None:
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
            failed_event = RepostEvent(
                repost_tweet_id="200",
                original_tweet_id="100",
                original_author_id="abc",
                repost_text="retry repost",
                original_text="retry post",
                media=[MediaItem(media_key="m1", media_type="photo", url="https://example.com/a.jpg")],
            )
            service.db.create_repost_event("200", "100")
            service.db.mark_failed("200", "download timeout")
            service.x_client = _FakeXClient([failed_event])
            service.telegram_client = _SuccessfulTelegramClient()

            with mock.patch("xdl_relay.service.download_file", return_value=Path(tmp) / "a.jpg"):
                result = service.process_once_with_stats()

            self.assertEqual(result["fetched"], 1)
            self.assertEqual(result["new"], 0)
            self.assertEqual(result["processed"], 1)


if __name__ == "__main__":
    unittest.main()
