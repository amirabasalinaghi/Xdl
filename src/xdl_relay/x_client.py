from __future__ import annotations

from urllib.parse import urlencode

from xdl_relay.http_utils import get_json, post_form_json
from xdl_relay.models import MediaItem, RepostEvent


class XClient:
    WEB_BEARER_TOKEN = (
        "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAA"
        "AAnNwIzUejRCOuH5WQxRilAbgTnQ6I"
        "8xnZC0Qh6F6YkQ9"
    )
    API_BASE_URL = "https://api.x.com/1.1"

    def __init__(
        self,
        timeout: int = 30,
        retries: int = 3,
        backoff_seconds: float = 1.0,
        max_pages: int = 5,
        web_bearer_token: str | None = None,
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.backoff_seconds = backoff_seconds
        self.max_pages = max_pages
        self._web_bearer_token = web_bearer_token or self.WEB_BEARER_TOKEN
        self._guest_token: str | None = None

    def get_new_reposts(self, user_id: str, since_id: str | None = None) -> list[RepostEvent]:
        events: list[RepostEvent] = []
        max_id: str | None = None
        pages = 0

        while pages < self.max_pages:
            params = {
                "user_id": user_id,
                "count": "200",
                "exclude_replies": "true",
                "include_rts": "true",
                "tweet_mode": "extended",
            }
            if since_id:
                params["since_id"] = since_id
            if max_id:
                params["max_id"] = max_id

            url = f"{self.API_BASE_URL}/statuses/user_timeline.json?{urlencode(params)}"
            payload = get_json(
                url,
                headers=self._web_headers(),
                timeout=self.timeout,
                retries=self.retries,
                backoff_seconds=self.backoff_seconds,
            )
            if not isinstance(payload, list) or not payload:
                break

            for tweet in payload:
                repost_ref = tweet.get("retweeted_status")
                if not repost_ref:
                    continue

                media_payload = repost_ref.get("extended_entities", {}).get("media", [])
                if not media_payload:
                    media_payload = repost_ref.get("entities", {}).get("media", [])

                media = [self._convert_media(item) for item in media_payload]
                media = [m for m in media if m is not None]

                if media:
                    events.append(
                        RepostEvent(
                            repost_tweet_id=tweet["id_str"],
                            original_tweet_id=repost_ref.get("id_str", str(repost_ref.get("id", ""))),
                            original_author_id=repost_ref.get("user", {}).get("id_str", "unknown"),
                            repost_text=tweet.get("full_text", tweet.get("text", "")),
                            original_text=repost_ref.get("full_text", repost_ref.get("text", "")),
                            media=media,
                        )
                    )

            pages += 1
            min_id = min(int(t["id_str"]) for t in payload if t.get("id_str"))
            max_id = str(min_id - 1)

        return sorted(events, key=lambda e: int(e.repost_tweet_id))

    def _web_headers(self) -> dict[str, str]:
        if not self._guest_token:
            self._guest_token = self._activate_guest_token()
        return {
            "Authorization": f"Bearer {self._web_bearer_token}",
            "x-guest-token": self._guest_token,
            "User-Agent": "Mozilla/5.0",
        }

    def _activate_guest_token(self) -> str:
        response = post_form_json(
            f"{self.API_BASE_URL}/guest/activate.json",
            form_data={},
            headers={"Authorization": f"Bearer {self._web_bearer_token}", "User-Agent": "Mozilla/5.0"},
            timeout=self.timeout,
        )
        token = response.get("guest_token")
        if not token:
            raise RuntimeError("Unable to fetch x-guest-token from X web API")
        return str(token)

    def _convert_media(self, media_payload: dict | None) -> MediaItem | None:
        if not media_payload:
            return None
        media_type = media_payload.get("type", "photo")
        media_key = media_payload.get("id_str") or str(media_payload.get("id", ""))
        if media_type == "photo":
            url = media_payload.get("media_url_https") or media_payload.get("media_url")
        else:
            variants = media_payload.get("video_info", {}).get("variants", [])
            mp4_variants = [v for v in variants if v.get("content_type") == "video/mp4" and v.get("url")]
            if not mp4_variants:
                return None
            mp4_variants.sort(key=lambda v: v.get("bitrate", v.get("bit_rate", 0)), reverse=True)
            url = mp4_variants[0]["url"]

        if not url or not media_key:
            return None

        return MediaItem(media_key=media_key, media_type=media_type, url=url)
