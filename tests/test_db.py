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


if __name__ == "__main__":
    unittest.main()
