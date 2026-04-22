from __future__ import annotations

import logging
from urllib.error import HTTPError
from urllib.parse import unquote, urlencode

from xdl_relay.enhancements import extract_best_media_variant
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
        max_pages: int = 100,
        page_size: int = 100,
        bearer_token: str = "",
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.backoff_seconds = backoff_seconds
        self.max_pages = max_pages
        self.page_size = min(100, max(5, page_size))
        self.bearer_token = bearer_token
        self._reverse_chronological_supported: bool | None = None

    def get_new_reposts(self, user_id: str, since_id: str | None = None) -> list[RepostEvent]:
        resolved_user_id = self._resolve_user_id(user_id)
        logger.info("Fetching reposts for user_id=%s resolved_user_id=%s since_id=%s", user_id, resolved_user_id, since_id)
        events = self._collect_reposts_for_endpoint(
            f"/users/{resolved_user_id}/tweets",
            since_id=since_id,
        )

        # Some accounts/tokens do not surface fresh repost actions consistently
        # through /users/{id}/tweets. Query reverse chronological timeline as an
        # additional source and merge results by repost tweet id.
        if self._reverse_chronological_supported is False:
            logger.debug("Reverse chronological fallback disabled from previous HTTP 403 response.")
            return events

        try:
            fallback_events = self._collect_reposts_for_endpoint(
                f"/users/{resolved_user_id}/timelines/reverse_chronological",
                since_id=since_id,
            )
            self._reverse_chronological_supported = True
            merged: dict[str, RepostEvent] = {event.repost_tweet_id: event for event in events}
            merged.update({event.repost_tweet_id: event for event in fallback_events})
            return sorted(merged.values(), key=lambda e: int(e.repost_tweet_id))
        except HTTPError as exc:
            # Application-only bearer tokens are rejected for this endpoint.
            # Keep polling functional by treating this fallback as optional.
            if exc.code == 403:
                self._reverse_chronological_supported = False
                logger.warning(
                    "Skipping reverse chronological fallback for user_id=%s due to HTTP 403. "
                    "Disabling this fallback for subsequent polls.",
                    resolved_user_id,
                )
                return events
            raise

    def _collect_reposts_for_endpoint(self, endpoint_path: str, since_id: str | None = None) -> list[RepostEvent]:
        events: list[RepostEvent] = []
        pagination_token: str | None = None
        pages = 0
        reached_page_limit = False

        while pages < self.max_pages:
            params = self._timeline_params(since_id=since_id, pagination_token=pagination_token)
            url = f"{self.API_BASE_URL}{endpoint_path}?{urlencode(params)}"
            logger.debug(
                "Requesting X timeline endpoint=%s page=%s next_token=%s",
                endpoint_path,
                pages + 1,
                pagination_token,
            )
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
            pagination_token = payload.get("meta", {}).get("next_token")
            if not pagination_token:
                break
            if pages >= self.max_pages:
                reached_page_limit = True
                break
        if reached_page_limit:
            logger.warning(
                "Stopped fetching endpoint=%s after max_pages=%s while next_token still exists. "
                "Increase X_MAX_PAGES to backfill more historical reposts.",
                endpoint_path,
                self.max_pages,
            )
        logger.info("Collected %s repost event(s) from endpoint=%s", len(events), endpoint_path)

        return sorted(events, key=lambda e: int(e.repost_tweet_id))

    def _timeline_params(self, since_id: str | None = None, pagination_token: str | None = None) -> dict[str, str]:
        params = {
            "max_results": str(self.page_size),
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
            media = [
                self._convert_media(included_media.get(media_key), fallback_key=media_key)
                for media_key in media_keys
            ]
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
            best_variant = extract_best_media_variant(media_payload)
            if not best_variant:
                return None
            url = best_variant.get("url")

        if not url or not media_key:
            return None

        return MediaItem(media_key=media_key, media_type=media_type, url=url)
