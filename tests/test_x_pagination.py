from __future__ import annotations

import unittest
from unittest.mock import patch

from xdl_relay.x_client import XClient


class TestXPagination(unittest.TestCase):
    def test_get_new_reposts_follows_max_id_pages(self) -> None:
        client = XClient(max_pages=5)

        page1 = [
            {
                "id_str": "101",
                "full_text": "RT one",
                "retweeted_status": {
                    "id_str": "500",
                    "user": {"id_str": "a1"},
                    "full_text": "orig1",
                    "extended_entities": {
                        "media": [{"id_str": "3_1", "type": "photo", "media_url_https": "https://x/img1.jpg"}]
                    },
                },
            },
        ]
        page2 = [
            {
                "id_str": "102",
                "full_text": "RT two",
                "retweeted_status": {
                    "id_str": "501",
                    "user": {"id_str": "a2"},
                    "full_text": "orig2",
                    "extended_entities": {
                        "media": [{"id_str": "3_2", "type": "photo", "media_url_https": "https://x/img2.jpg"}]
                    },
                },
            },
        ]

        with patch("xdl_relay.x_client.XClient._activate_guest_token", return_value="guest"), patch(
            "xdl_relay.x_client.get_json", side_effect=[page1, page2, []]
        ) as mock_get:
            events = client.get_new_reposts("u1")

        self.assertEqual(len(events), 2)
        self.assertEqual([e.repost_tweet_id for e in events], ["101", "102"])
        self.assertEqual(mock_get.call_count, 3)


if __name__ == "__main__":
    unittest.main()
