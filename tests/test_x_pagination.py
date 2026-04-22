from __future__ import annotations

import unittest
from unittest.mock import patch

from xdl_relay.x_client import XClient


class TestXPagination(unittest.TestCase):
    def test_get_new_reposts_follows_max_id_pages(self) -> None:
        client = XClient(max_pages=5, client_id="cid")

        page1 = {
            "data": [{"id": "101", "text": "RT one", "referenced_tweets": [{"type": "retweeted", "id": "500"}]}],
            "includes": {
                "tweets": [{"id": "500", "author_id": "a1", "text": "orig1", "attachments": {"media_keys": ["3_1"]}}],
                "media": [{"media_key": "3_1", "type": "photo", "url": "https://x/img1.jpg"}],
            },
            "meta": {"next_token": "p2"},
        }
        page2 = {
            "data": [{"id": "102", "text": "RT two", "referenced_tweets": [{"type": "retweeted", "id": "501"}]}],
            "includes": {
                "tweets": [{"id": "501", "author_id": "a2", "text": "orig2", "attachments": {"media_keys": ["3_2"]}}],
                "media": [{"media_key": "3_2", "type": "photo", "url": "https://x/img2.jpg"}],
            },
            "meta": {},
        }

        token = type("FakeToken", (), {"access_token": "abc", "is_expired": lambda self: False})()
        with patch("xdl_relay.x_client.OAuthTokenStore.load", return_value=token), patch(
            "xdl_relay.x_client.get_json", side_effect=[page1, page2]
        ) as mock_get:
            events = client.get_new_reposts("u1")

        self.assertEqual(len(events), 2)
        self.assertEqual([e.repost_tweet_id for e in events], ["101", "102"])
        self.assertEqual(mock_get.call_count, 2)


if __name__ == "__main__":
    unittest.main()
