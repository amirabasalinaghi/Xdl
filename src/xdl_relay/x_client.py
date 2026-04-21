from __future__ import annotations

from urllib.parse import urlencode

from xdl_relay.http_utils import get_json
from xdl_relay.models import MediaItem, RepostEvent


class XClient:
    BASE_URL = "https://api.x.com/2"

    def __init__(self, bearer_token: str) -> None:
        self._headers = {"Authorization": f"Bearer {bearer_token}"}

    def get_new_reposts(self, user_id: str, since_id: str | None = None) -> list[RepostEvent]:
        params = {
            "exclude": "replies",
            "max_results": "20",
            "tweet.fields": "referenced_tweets,author_id,attachments",
            "expansions": "referenced_tweets.id,referenced_tweets.id.attachments.media_keys,attachments.media_keys",
            "media.fields": "type,url,variants",
        }
        if since_id:
            params["since_id"] = since_id

        url = f"{self.BASE_URL}/users/{user_id}/tweets?{urlencode(params)}"
        payload = get_json(url, headers=self._headers)

        data = payload.get("data", [])
        includes = payload.get("includes", {})
        referenced_tweets = {t["id"]: t for t in includes.get("tweets", [])}
        media_by_key = {m["media_key"]: m for m in includes.get("media", [])}

        events: list[RepostEvent] = []
        for tweet in data:
            refs = tweet.get("referenced_tweets", [])
            repost_ref = next((r for r in refs if r.get("type") in {"retweeted", "reposted"}), None)
            if not repost_ref:
                continue

            original = referenced_tweets.get(repost_ref["id"], {"id": repost_ref["id"], "author_id": "unknown"})
            media_keys = original.get("attachments", {}).get("media_keys", [])
            media = [self._convert_media(media_by_key.get(key), key) for key in media_keys]
            media = [m for m in media if m is not None]

            if media:
                events.append(
                    RepostEvent(
                        repost_tweet_id=tweet["id"],
                        original_tweet_id=original["id"],
                        original_author_id=original.get("author_id", "unknown"),
                        media=media,
                    )
                )

        return sorted(events, key=lambda e: int(e.repost_tweet_id))

    def _convert_media(self, media_payload: dict | None, media_key: str) -> MediaItem | None:
        if not media_payload:
            return None
        media_type = media_payload.get("type", "photo")
        if media_type == "photo":
            url = media_payload.get("url")
        else:
            variants = media_payload.get("variants", [])
            mp4_variants = [v for v in variants if v.get("content_type") == "video/mp4" and v.get("url")]
            if not mp4_variants:
                return None
            mp4_variants.sort(key=lambda v: v.get("bit_rate", 0), reverse=True)
            url = mp4_variants[0]["url"]

        if not url:
            return None

        return MediaItem(media_key=media_key, media_type=media_type, url=url)
