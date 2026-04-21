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
    def send_media(self, chat_id: str, files: list[Path]) -> list[int]:
        raise RuntimeError("telegram down")


class TestServiceBehavior(unittest.TestCase):
    def test_failed_event_does_not_advance_last_seen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(
                x_bearer_token="x",
                x_user_id="user",
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
                        media=[MediaItem(media_key="m1", media_type="photo", url="https://example.com/a.jpg")],
                    )
                ]
            )
            service.telegram_client = _FailingTelegramClient()

            processed = service.process_once()
            self.assertEqual(processed, 0)
            self.assertIsNone(service.db.get_last_seen_tweet_id())


if __name__ == "__main__":
    unittest.main()
