from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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


class TestServiceBehavior(unittest.TestCase):
    def test_failed_event_does_not_advance_last_seen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(
                x_user_id="user",
                x_client_id="cid",
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
            self.assertIsNone(service.db.get_last_seen_tweet_id())
            self.assertTrue(telegram.messages)

    def test_caption_contains_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(
                x_user_id="user",
                x_client_id="cid",
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


if __name__ == "__main__":
    unittest.main()
