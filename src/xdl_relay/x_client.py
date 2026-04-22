from __future__ import annotations

import logging
from urllib.error import HTTPError
from urllib.parse import unquote, urlencode

from xdl_relay.http_utils import get_json
from xdl_relay.models import MediaItem, RepostEvent

logger = logging.getLogger(__name__)


class XClient:
    API_BASE_URL = "https://api.x.com/2"

    def __init__(
        self,
        timeout: int = 30,
        retries: int = 3,
        backoff_seconds: float = 1.0,
        max_pages: int = 5,
        bearer_token: str = "",
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.backoff_seconds = backoff_seconds
        self.max_pages = max_pages
        self.bearer_token = bearer_token

    def get_new_reposts(self, user_id: str, since_id: str | None = None) -> list[RepostEvent]:
        resolved_user_id = self._resolve_user_id(user_id)
        logger.info("Fetching reposts for user_id=%s resolved_user_id=%s since_id=%s", user_id, resolved_user_id, since_id)
        events = self._collect_reposts_for_endpoint(
            f"/users/{resolved_user_id}/tweets",
            since_id=since_id,
        )
        if events:
            return events

        # Fallback: X docs describe reverse chronological timeline as including
        # reposts/retweets in feed order. Some accounts/tokens do not surface
        # fresh repost actions consistently through /users/{id}/tweets.
        return self._collect_reposts_for_endpoint(
            f"/users/{resolved_user_id}/timelines/reverse_chronological",
            since_id=since_id,
        )

    def _collect_reposts_for_endpoint(self, endpoint_path: str, since_id: str | None = None) -> list[RepostEvent]:
        events: list[RepostEvent] = []
        max_id: str | None = None
        pages = 0

        while pages < self.max_pages:
            params = self._timeline_params(since_id=since_id, pagination_token=max_id)
            url = f"{self.API_BASE_URL}{endpoint_path}?{urlencode(params)}"
            logger.debug("Requesting X timeline endpoint=%s page=%s next_token=%s", endpoint_path, pages + 1, max_id)
            payload = get_json(
                url,
                headers=self._auth_headers(),
                timeout=self.timeout,
                retries=self.retries,
                backoff_seconds=self.backoff_seconds,
            )
            tweets = payload.get("data", [])
            if not tweets:
                logger.debug("No tweets returned endpoint=%s page=%s", endpoint_path, pages + 1)
                break

            events.extend(self._extract_repost_events(tweets, payload))
            pages += 1
            max_id = payload.get("meta", {}).get("next_token")
            if not max_id:
                break
        logger.info("Collected %s repost event(s) from endpoint=%s", len(events), endpoint_path)

        return sorted(events, key=lambda e: int(e.repost_tweet_id))

    def _timeline_params(self, since_id: str | None = None, pagination_token: str | None = None) -> dict[str, str]:
        params = {
            "max_results": "100",
            "exclude": "replies",
            "tweet.fields": "text,author_id,referenced_tweets,attachments",
            "expansions": (
                "attachments.media_keys,"
                "referenced_tweets.id,"
                "referenced_tweets.id.author_id,"
                "referenced_tweets.id.attachments.media_keys"
            ),
            "media.fields": "type,url,variants",
        }
        if since_id:
            params["since_id"] = since_id
        if pagination_token:
            params["pagination_token"] = pagination_token
        return params

    def _extract_repost_events(self, tweets: list[dict], payload: dict) -> list[RepostEvent]:
        events: list[RepostEvent] = []
        included_tweets = {t["id"]: t for t in payload.get("includes", {}).get("tweets", []) if t.get("id")}
        included_media = {m["media_key"]: m for m in payload.get("includes", {}).get("media", []) if m.get("media_key")}

        for tweet in tweets:
            references = tweet.get("referenced_tweets", [])
            retweet_ref = next(
                (ref for ref in references if ref.get("type") in {"retweeted", "reposted"}),
                None,
            )
            if not retweet_ref:
                continue
            repost_ref = included_tweets.get(retweet_ref.get("id", ""))
            if not repost_ref:
                continue
            media_keys = repost_ref.get("attachments", {}).get("media_keys", [])
            media_payload = [included_media.get(media_key) for media_key in media_keys if media_key in included_media]

            media = [self._convert_media(item, fallback_key=key) for item, key in zip(media_payload, media_keys)]
            media = [m for m in media if m is not None]

            if media:
                events.append(
                    RepostEvent(
                        repost_tweet_id=tweet["id"],
                        original_tweet_id=repost_ref.get("id", ""),
                        original_author_id=repost_ref.get("author_id", "unknown"),
                        repost_text=tweet.get("text", ""),
                        original_text=repost_ref.get("text", ""),
                        media=media,
                    )
                )
        return events

    def _resolve_user_id(self, user_id: str) -> str:
        normalized = user_id.strip()
        if normalized.isdigit():
            return normalized

        username = normalized.lstrip("@")
        url = f"{self.API_BASE_URL}/users/by/username/{username}"
        try:
            payload = get_json(
                url,
                headers=self._auth_headers(),
                timeout=self.timeout,
                retries=self.retries,
                backoff_seconds=self.backoff_seconds,
            )
            resolved = payload.get("data", {}).get("id", "")
            if resolved:
                return str(resolved)
        except HTTPError:
            # Preserve the original value for backward compatibility and
            # allow API errors to be surfaced by get_new_reposts.
            pass

        return normalized

    def _auth_headers(self) -> dict[str, str]:
        if not self.bearer_token:
            raise RuntimeError("X bearer token is missing. Set X_BEARER_TOKEN.")
        token = self._normalize_bearer_token(self.bearer_token)
        if not token:
            raise RuntimeError("X bearer token is missing. Set X_BEARER_TOKEN.")
        return {
            "Authorization": f"Bearer {token}",
            "User-Agent": "Mozilla/5.0",
        }

    def _normalize_bearer_token(self, token: str) -> str:
        normalized = unquote((token or "").strip())
        if normalized.lower().startswith("bearer "):
            normalized = normalized[7:].strip()
        return normalized

    def _convert_media(self, media_payload: dict | None, fallback_key: str = "") -> MediaItem | None:
        if not media_payload:
            return None
        media_type = media_payload.get("type", "photo")
        media_key = media_payload.get("media_key", fallback_key)
        if media_type == "photo":
            url = media_payload.get("url")
        else:
            variants = media_payload.get("video_info", {}).get("variants", [])
            if not variants:
                variants = media_payload.get("variants", [])
            mp4_variants = [v for v in variants if v.get("content_type") == "video/mp4" and v.get("url")]
            if not mp4_variants:
                return None
            mp4_variants.sort(key=lambda v: v.get("bitrate", v.get("bit_rate", 0)), reverse=True)
            url = mp4_variants[0]["url"]

        if not url or not media_key:
            return None

        return MediaItem(media_key=media_key, media_type=media_type, url=url)
