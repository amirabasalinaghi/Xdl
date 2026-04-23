from __future__ import annotations

import unittest
from urllib.error import HTTPError
from unittest.mock import patch

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
        feed_payload = {"data": [], "meta": {}}
        with patch(
            "xdl_relay.x_client.get_json",
            side_effect=[user_payload, feed_payload],
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

    def test_get_new_reposts_accepts_retweet_like_reference_types(self) -> None:
        client = XClient(max_pages=1, bearer_token="token")
        payload = {
            "data": [{"id": "212", "text": "repost", "referenced_tweets": [{"type": "retweet", "id": "701"}]}],
            "includes": {
                "tweets": [{"id": "701", "author_id": "99", "text": "orig", "attachments": {"media_keys": ["3_701"]}}],
                "media": [{"media_key": "3_701", "type": "photo", "url": "https://x/img.jpg"}],
            },
            "meta": {},
        }

        with patch("xdl_relay.x_client.get_json", return_value=payload):
            events = client.get_new_reposts("1")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].repost_tweet_id, "212")

    def test_get_new_reposts_with_stats_counts_profile_post_types(self) -> None:
        client = XClient(max_pages=1, bearer_token="token")
        payload = {
            "data": [
                {"id": "1", "text": "original", "author_id": "1"},
                {"id": "2", "text": "reply", "referenced_tweets": [{"type": "replied_to", "id": "1"}]},
                {"id": "3", "text": "quote", "referenced_tweets": [{"type": "quoted", "id": "1"}]},
                {"id": "4", "text": "repost", "referenced_tweets": [{"type": "retweeted", "id": "1"}]},
            ],
            "includes": {
                "tweets": [{"id": "1", "author_id": "1", "text": "original", "attachments": {"media_keys": ["3_1"]}}],
                "media": [{"media_key": "3_1", "type": "photo", "url": "https://x/img1.jpg"}],
            },
            "meta": {},
        }

        with patch("xdl_relay.x_client.get_json", return_value=payload):
            _events, stats = client.get_new_reposts_with_stats("1")

        self.assertEqual(stats["total_profile_posts_seen"], 4)
        self.assertEqual(stats["total_reposts_seen"], 1)
        self.assertEqual(stats["total_replies_seen"], 1)
        self.assertEqual(stats["total_quotes_seen"], 1)
        self.assertEqual(stats["total_original_posts_seen"], 1)

    def test_extract_repost_events_fetches_missing_media_keys_for_included_tweet(self) -> None:
        client = XClient(max_pages=1, bearer_token="token")
        payload = {
            "data": [{"id": "302", "text": "repost", "referenced_tweets": [{"type": "retweeted", "id": "901"}]}],
            "includes": {
                "tweets": [{"id": "901", "author_id": "a9", "text": "orig", "attachments": {"media_keys": ["3_a", "3_b"]}}],
                "media": [{"media_key": "3_a", "type": "photo", "url": "https://x/img-a.jpg"}],
            },
            "meta": {},
        }

        with patch.object(
            client,
            "_fetch_tweet_with_media",
            return_value=(
                {"id": "901", "author_id": "a9", "text": "orig", "attachments": {"media_keys": ["3_a", "3_b"]}},
                {"3_b": {"media_key": "3_b", "type": "photo", "url": "https://x/img-b.jpg"}},
            ),
        ) as mock_fetch:
            events = client._extract_repost_events(payload["data"], payload)

        self.assertEqual(len(events), 1)
        self.assertEqual([m.media_key for m in events[0].media], ["3_a", "3_b"])
        mock_fetch.assert_called_once_with("901")

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

        with patch.object(client, "_fetch_tweet_with_media", return_value=({}, {})):
            events = client._extract_repost_events(payload["data"], payload)

        self.assertEqual(len(events), 1)
        self.assertEqual(len(events[0].media), 1)
        self.assertEqual(events[0].media[0].media_key, "3_ok")

    def test_extract_repost_events_includes_non_repost_replies_with_media(self) -> None:
        client = XClient(max_pages=1, bearer_token="token")
        payload = {
            "data": [
                {
                    "id": "401",
                    "text": "reply with media",
                    "author_id": "1",
                    "referenced_tweets": [{"type": "replied_to", "id": "120"}],
                    "attachments": {"media_keys": ["3_401"]},
                }
            ],
            "includes": {
                "tweets": [],
                "media": [{"media_key": "3_401", "type": "photo", "url": "https://x/img401.jpg"}],
            },
            "meta": {},
        }

        events = client._extract_repost_events(payload["data"], payload)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].repost_tweet_id, "401")
        self.assertEqual(events[0].original_tweet_id, "401")

    def test_extract_repost_events_uses_parent_reply_media_when_reply_has_none(self) -> None:
        client = XClient(max_pages=1, bearer_token="token")
        payload = {
            "data": [
                {
                    "id": "402",
                    "text": "reply no media",
                    "author_id": "1",
                    "referenced_tweets": [{"type": "replied_to", "id": "121"}],
                }
            ],
            "includes": {
                "tweets": [{"id": "121", "author_id": "2", "text": "parent with media", "attachments": {"media_keys": ["3_121"]}}],
                "media": [{"media_key": "3_121", "type": "photo", "url": "https://x/img121.jpg"}],
            },
            "meta": {},
        }

        events = client._extract_repost_events(payload["data"], payload)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].repost_tweet_id, "402")
        self.assertEqual(events[0].original_tweet_id, "121")
        self.assertEqual(events[0].media[0].media_key, "3_121")

    def test_convert_media_video_falls_back_to_first_variant_url(self) -> None:
        client = XClient(max_pages=1, bearer_token="token")
        media = {
            "media_key": "3_fallback",
            "type": "video",
            "variants": [
                {"content_type": "application/x-mpegURL", "url": "https://video/master.m3u8"},
            ],
        }

        converted = client._convert_media(media)

        self.assertIsNotNone(converted)
        self.assertEqual(converted.url, "https://video/master.m3u8")

    def test_get_new_reposts_includes_profile_post_media(self) -> None:
        client = XClient(max_pages=1, bearer_token="token")
        profile_payload = {
            "data": [{"id": "205", "text": "my post", "author_id": "1", "attachments": {"media_keys": ["3_5"]}}],
            "includes": {
                "tweets": [],
                "media": [{"media_key": "3_5", "type": "photo", "url": "https://x/img5.jpg"}],
            },
            "meta": {},
        }

        with patch("xdl_relay.x_client.get_json", return_value=profile_payload) as mock_get:
            events = client.get_new_reposts("1")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].repost_tweet_id, "205")
        self.assertEqual(events[0].original_tweet_id, "205")
        self.assertIn("/users/1/tweets?", mock_get.call_args_list[0].args[0])

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

    def test_extract_repost_events_walks_all_references_for_media(self) -> None:
        client = XClient(max_pages=1, bearer_token="token")
        payload = {
            "data": [
                {
                    "id": "100",
                    "text": "complex repost",
                    "referenced_tweets": [{"type": "retweeted", "id": "500"}],
                }
            ],
            "includes": {
                "tweets": [
                    {
                        "id": "500",
                        "author_id": "99",
                        "text": "mid",
                        "referenced_tweets": [
                            {"type": "quoted", "id": "700"},
                            {"type": "replied_to", "id": "800"},
                        ],
                    },
                    {"id": "700", "author_id": "42", "text": "no media branch"},
                    {"id": "800", "author_id": "88", "text": "has media", "attachments": {"media_keys": ["3_8"]}},
                ],
                "media": [{"media_key": "3_8", "type": "photo", "url": "https://x/img8.jpg"}],
            },
            "meta": {},
        }

        events = client._extract_repost_events(payload["data"], payload)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].repost_tweet_id, "100")
        self.assertEqual(events[0].original_tweet_id, "800")
        self.assertEqual(events[0].media[0].url, "https://x/img8.jpg")

    def test_get_new_reposts_timeline_401_has_actionable_error(self) -> None:
        client = XClient(max_pages=1, bearer_token="token")
        http_error = HTTPError(
            url="https://api.x.com/2/users/1/tweets",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=None,
        )
        with patch("xdl_relay.x_client.get_json", side_effect=http_error):
            with self.assertRaises(RuntimeError) as ctx:
                client.get_new_reposts("1")

        self.assertIn("token can belong to a different x user", str(ctx.exception).lower())

    def test_get_new_reposts_stays_on_single_account_feed(self) -> None:
        client = XClient(max_pages=1, bearer_token="token")
        profile_payload = {
            "data": [{"id": "777", "text": "RT", "referenced_tweets": [{"type": "retweeted", "id": "900"}]}],
            "includes": {
                "tweets": [{"id": "900", "author_id": "a9", "text": "orig", "attachments": {"media_keys": ["3_m"]}}],
                "media": [{"media_key": "3_m", "type": "photo", "url": "https://x/img-m.jpg"}],
            },
            "meta": {},
        }

        with patch("xdl_relay.x_client.get_json", side_effect=[profile_payload, profile_payload]) as mock_get:
            client.get_new_reposts("1")
            client.get_new_reposts("1")

        requested_urls = [call.args[0] for call in mock_get.call_args_list]
        self.assertEqual(len(requested_urls), 2)
        self.assertTrue(all("/users/1/tweets?" in url for url in requested_urls))


if __name__ == "__main__":
    unittest.main()
