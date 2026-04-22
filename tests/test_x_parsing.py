from __future__ import annotations

import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from xdl_relay.x_client import XClient


class TestXParsing(unittest.TestCase):
    def test_default_max_pages_is_backfill_friendly(self) -> None:
        client = XClient()
        self.assertEqual(client.max_pages, 100)

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
        with patch(
            "xdl_relay.x_client.get_json",
            side_effect=[user_payload, timeline_payload, timeline_payload],
        ) as mock_get:
            events = client.get_new_reposts("@example_user")

        self.assertEqual(events, [])
        self.assertIn("/users/by/username/example_user", mock_get.call_args_list[0].args[0])
        self.assertIn("/users/123/tweets?", mock_get.call_args_list[1].args[0])

    def test_get_new_reposts_accepts_reposted_reference_type(self) -> None:
        client = XClient(max_pages=1, bearer_token="token")
        timeline_payload = {
            "data": [{"id": "201", "text": "repost", "referenced_tweets": [{"type": "reposted", "id": "700"}]}],
            "includes": {
                "tweets": [{"id": "700", "author_id": "a1", "text": "orig", "attachments": {"media_keys": ["3_9"]}}],
                "media": [{"media_key": "3_9", "type": "photo", "url": "https://x/img9.jpg"}],
            },
            "meta": {},
        }

        with patch("xdl_relay.x_client.get_json", return_value=timeline_payload):
            events = client.get_new_reposts("1")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].repost_tweet_id, "201")
    def test_extract_repost_events_preserves_media_key_when_some_media_missing(self) -> None:
        client = XClient(max_pages=1, bearer_token="token")
        payload = {
            "data": [{"id": "301", "text": "repost", "referenced_tweets": [{"type": "retweeted", "id": "900"}]}],
            "includes": {
                "tweets": [{"id": "900", "author_id": "a9", "text": "orig", "attachments": {"media_keys": ["3_missing", "3_ok"]}}],
                "media": [{"media_key": "3_ok", "type": "photo", "url": "https://x/img-ok.jpg"}],
            },
            "meta": {},
        }

        events = client._extract_repost_events(payload["data"], payload)

        self.assertEqual(len(events), 1)
        self.assertEqual(len(events[0].media), 1)
        self.assertEqual(events[0].media[0].media_key, "3_ok")

    def test_get_new_reposts_falls_back_to_reverse_chron_timeline(self) -> None:
        client = XClient(max_pages=1, bearer_token="token")
        empty_user_posts = {"data": [], "meta": {}}
        fallback_payload = {
            "data": [{"id": "205", "text": "repost", "referenced_tweets": [{"type": "retweeted", "id": "705"}]}],
            "includes": {
                "tweets": [{"id": "705", "author_id": "a2", "text": "orig", "attachments": {"media_keys": ["3_5"]}}],
                "media": [{"media_key": "3_5", "type": "photo", "url": "https://x/img5.jpg"}],
            },
            "meta": {},
        }

        with patch("xdl_relay.x_client.get_json", side_effect=[empty_user_posts, fallback_payload]) as mock_get:
            events = client.get_new_reposts("1")

        self.assertEqual(len(events), 1)
        self.assertIn("/users/1/tweets?", mock_get.call_args_list[0].args[0])
        self.assertIn("/users/1/timelines/reverse_chronological?", mock_get.call_args_list[1].args[0])

    def test_get_new_reposts_ignores_403_from_fallback_timeline(self) -> None:
        client = XClient(max_pages=1, bearer_token="token")
        empty_user_posts = {"data": [], "meta": {}}
        forbidden = HTTPError(
            "https://api.x.com/2/users/1/timelines/reverse_chronological",
            403,
            "Forbidden",
            hdrs=None,
            fp=None,
        )

        with patch("xdl_relay.x_client.get_json", side_effect=[empty_user_posts, forbidden]):
            events = client.get_new_reposts("1")

        self.assertEqual(events, [])

    def test_get_new_reposts_disables_fallback_after_403(self) -> None:
        client = XClient(max_pages=1, bearer_token="token")
        empty_user_posts = {"data": [], "meta": {}}
        forbidden = HTTPError(
            "https://api.x.com/2/users/1/timelines/reverse_chronological",
            403,
            "Forbidden",
            hdrs=None,
            fp=None,
        )

        with patch(
            "xdl_relay.x_client.get_json",
            side_effect=[empty_user_posts, forbidden, empty_user_posts],
        ) as mock_get:
            first = client.get_new_reposts("1")
            second = client.get_new_reposts("1")

        self.assertEqual(first, [])
        self.assertEqual(second, [])
        self.assertEqual(mock_get.call_count, 3)
        self.assertIn("/users/1/tweets?", mock_get.call_args_list[2].args[0])

    def test_extract_repost_events_fetches_missing_referenced_tweet(self) -> None:
        client = XClient(max_pages=1, bearer_token="token")
        payload = {
            "data": [{"id": "100", "text": "repost", "referenced_tweets": [{"type": "retweeted", "id": "500"}]}],
            "includes": {"tweets": [], "media": []},
            "meta": {},
        }

        with patch.object(
            client,
            "_fetch_tweet_with_media",
            return_value=(
                {"id": "500", "author_id": "99", "text": "orig", "attachments": {"media_keys": ["3_1"]}},
                {"3_1": {"media_key": "3_1", "type": "photo", "url": "https://cdn/x.jpg"}},
            ),
        ) as mock_fetch:
            events = client._extract_repost_events(payload["data"], payload)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].repost_tweet_id, "100")
        self.assertEqual(events[0].original_tweet_id, "500")
        self.assertEqual(events[0].media[0].url, "https://cdn/x.jpg")
        mock_fetch.assert_called_once_with("500")


if __name__ == "__main__":
    unittest.main()
