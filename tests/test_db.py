from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from xdl_relay.db import RelayDB


class TestRelayDB(unittest.TestCase):
    def test_db_state_and_dedupe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "relay.db")
            db = RelayDB(db_path)

            self.assertIsNone(db.get_last_seen_tweet_id())
            db.set_last_seen_tweet_id("100")
            self.assertEqual(db.get_last_seen_tweet_id(), "100")

            self.assertTrue(db.create_repost_event("200", "150"))
            self.assertFalse(db.create_repost_event("200", "150"))

    def test_list_unsent_repost_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "relay.db")
            db = RelayDB(db_path)
            db.create_repost_event("100", "90")
            db.create_repost_event("101", "91")
            db.create_repost_event("102", "92")
            db.mark_failed("101", "failed to send")
            db.mark_sent("102", "1,2")

            unsent = db.list_unsent_repost_ids()
            self.assertIn("100", unsent)
            self.assertIn("101", unsent)
            self.assertNotIn("102", unsent)

    def test_failure_counters_and_manual_retry_reset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "relay.db")
            db = RelayDB(db_path)
            db.create_repost_event("100", "90")

            self.assertEqual(db.mark_failed("100", "network"), 1)
            self.assertEqual(db.mark_failed("100", "network"), 2)
            self.assertEqual(db.get_repost_failure_count("100"), 2)
            self.assertFalse(db.was_failure_notified("100"))

            db.mark_failure_notified("100")
            self.assertTrue(db.was_failure_notified("100"))

            self.assertEqual(db.reset_failed_attempts(), 1)
            self.assertEqual(db.get_repost_failure_count("100"), 0)
            self.assertFalse(db.was_failure_notified("100"))

    def test_monitored_user_checkpoint_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "relay.db")
            db = RelayDB(db_path)

            self.assertIsNone(db.get_monitored_user_id())
            db.set_monitored_user_id("user-a")
            self.assertEqual(db.get_monitored_user_id(), "user-a")
            db.set_last_seen_tweet_id(None)
            self.assertIsNone(db.get_last_seen_tweet_id())

    def test_reset_runtime_history_clears_dashboard_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "relay.db")
            db = RelayDB(db_path)
            db.create_repost_event("100", "90")
            db.mark_sent("100", "1")
            db.upsert_media_index("90", "m1", "photo", "https://example.com/p1.jpg", "/tmp/p1.jpg")

            self.assertEqual(db.get_overview()["total_events"], 1)
            self.assertEqual(db.get_overview()["total_media_seen"], 1)

            db.reset_runtime_history()

            self.assertEqual(db.get_overview()["total_events"], 0)
            self.assertEqual(db.get_overview()["total_media_seen"], 0)

    def test_overview_includes_seen_post_and_media_counters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "relay.db")
            db = RelayDB(db_path)

            db.create_repost_event("100", "90")
            db.create_repost_event("101", "91")
            db.upsert_media_index("90", "m1", "photo", "https://example.com/p1.jpg", "/tmp/p1.jpg")
            db.upsert_media_index("91", "m2", "video", "https://example.com/v1.mp4", "/tmp/v1.mp4")
            db.upsert_media_index("91", "m3", "animated_gif", "https://example.com/g1.mp4", "/tmp/g1.mp4")

            overview = db.get_overview()

            self.assertEqual(overview["total_events"], 2)
            self.assertEqual(overview["total_media_seen"], 3)
            self.assertEqual(overview["total_photos_seen"], 1)
            self.assertEqual(overview["total_videos_seen"], 2)


if __name__ == "__main__":
    unittest.main()
