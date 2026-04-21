from __future__ import annotations

import unittest
from unittest.mock import patch

from xdl_relay.x_client import XClient


class TestXPagination(unittest.TestCase):
    def test_get_new_reposts_follows_next_token(self) -> None:
        client = XClient("token", max_pages=5)

        page1 = {
            "data": [
                {"id": "101", "referenced_tweets": [{"type": "retweeted", "id": "500"}]},
            ],
            "includes": {
                "tweets": [{"id": "500", "author_id": "a1", "attachments": {"media_keys": ["3_1"]}}],
                "media": [{"media_key": "3_1", "type": "photo", "url": "https://x/img1.jpg"}],
            },
            "meta": {"next_token": "NEXT"},
        }
        page2 = {
            "data": [
                {"id": "102", "referenced_tweets": [{"type": "retweeted", "id": "501"}]},
            ],
            "includes": {
                "tweets": [{"id": "501", "author_id": "a2", "attachments": {"media_keys": ["3_2"]}}],
                "media": [{"media_key": "3_2", "type": "photo", "url": "https://x/img2.jpg"}],
            },
            "meta": {},
        }

        with patch("xdl_relay.x_client.get_json", side_effect=[page1, page2]) as mock_get:
            events = client.get_new_reposts("u1")

        self.assertEqual(len(events), 2)
        self.assertEqual([e.repost_tweet_id for e in events], ["101", "102"])
        self.assertEqual(mock_get.call_count, 2)


if __name__ == "__main__":
    unittest.main()
