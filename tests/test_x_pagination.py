from __future__ import annotations

import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from xdl_relay.x_client import XClient


class TestXPagination(unittest.TestCase):
    def test_get_new_reposts_follows_max_id_pages(self) -> None:
        client = XClient(max_pages=5, bearer_token="token")

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

        with patch(
            "xdl_relay.x_client.get_json",
            side_effect=[page1, page2, {"data": [], "meta": {}}],
        ) as mock_get:
            events = client.get_new_reposts("1")

        self.assertEqual(len(events), 2)
        self.assertEqual([e.repost_tweet_id for e in events], ["101", "102"])
        self.assertEqual(mock_get.call_count, 3)

    def test_get_new_reposts_uses_valid_expansions(self) -> None:
        client = XClient(max_pages=1, bearer_token="token")
        payload = {"data": [], "meta": {}}

        with patch("xdl_relay.x_client.get_json", return_value=payload) as mock_get:
            client.get_new_reposts("1")

        requested_url = mock_get.call_args_list[0].args[0]
        query = parse_qs(urlparse(requested_url).query)
        expansions = query.get("expansions", [""])[0]
        media_fields = query.get("media.fields", [""])[0]
        self.assertNotIn("exclude", query)
        self.assertIn("attachments.media_keys", expansions)
        self.assertIn("referenced_tweets.id.attachments.media_keys", expansions)
        self.assertNotIn("video_info", media_fields)

    def test_get_new_reposts_logs_skipped_non_media_posts(self) -> None:
        client = XClient(max_pages=1, bearer_token="token")
        payload = {
            "data": [
                {"id": "101", "text": "plain post without media"},
            ],
            "meta": {},
        }

        with patch(
            "xdl_relay.x_client.get_json",
            side_effect=[payload, {"data": [], "meta": {}}],
        ):
            with self.assertLogs("xdl_relay.x_client", level="INFO") as logs:
                events = client.get_new_reposts("1")

        self.assertEqual(events, [])
        self.assertTrue(
            any("Skipped 1 post(s)" in message for message in logs.output),
            "expected skipped-non-media log message to be emitted",
        )


if __name__ == "__main__":
    unittest.main()
