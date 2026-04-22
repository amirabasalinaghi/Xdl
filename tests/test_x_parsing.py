from __future__ import annotations

import unittest
from unittest.mock import patch

from xdl_relay.x_client import XClient


class TestXParsing(unittest.TestCase):
    def test_convert_media_selects_best_variant(self) -> None:
        client = XClient()
        media = {
            "media_key": "3_1",
            "type": "video",
            "variants": [
                {"content_type": "video/mp4", "bitrate": 256000, "url": "http://low.mp4"},
                {"content_type": "video/mp4", "bitrate": 832000, "url": "http://high.mp4"},
            ],
        }

        converted = client._convert_media(media)
        self.assertIsNotNone(converted)
        self.assertEqual(converted.url, "http://high.mp4")


    def test_auth_headers_decode_urlencoded_bearer_token(self) -> None:
        client = XClient(bearer_token="AAAA%2FBBBB%3D")

        headers = client._auth_headers()

        self.assertEqual(headers["Authorization"], "Bearer AAAA/BBBB=")

    def test_auth_headers_strip_bearer_prefix(self) -> None:
        client = XClient(bearer_token="Bearer token-value")

        headers = client._auth_headers()

        self.assertEqual(headers["Authorization"], "Bearer token-value")
    def test_get_new_reposts_resolves_username_to_user_id(self) -> None:
        client = XClient(max_pages=1, bearer_token="token")
        user_payload = {"data": {"id": "123"}}
        timeline_payload = {"data": [], "meta": {}}
        with patch("xdl_relay.x_client.get_json", side_effect=[user_payload, timeline_payload]) as mock_get:
            events = client.get_new_reposts("@example_user")

        self.assertEqual(events, [])
        self.assertIn("/users/by/username/example_user", mock_get.call_args_list[0].args[0])
        self.assertIn("/users/123/tweets?", mock_get.call_args_list[1].args[0])


if __name__ == "__main__":
    unittest.main()
