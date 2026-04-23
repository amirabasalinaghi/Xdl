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
        self._reverse_timeline_enabled = True

    def get_new_reposts(self, user_id: str, since_id: str | None = None) -> list[RepostEvent]:
        resolved_user_id = self._resolve_user_id(user_id)
        logger.info("Fetching reposts for user_id=%s resolved_user_id=%s since_id=%s", user_id, resolved_user_id, since_id)
        profile_events = self._collect_reposts_for_endpoint(
            f"/users/{resolved_user_id}/tweets",
            since_id=since_id,
        )
        timeline_events: list[RepostEvent] = []
        timeline_endpoint = f"/users/{resolved_user_id}/timelines/reverse_chronological"
        if self._reverse_timeline_enabled:
            try:
                timeline_events = self._collect_reposts_for_endpoint(
                    timeline_endpoint,
                    since_id=since_id,
                )
            except RuntimeError as exc:
                message = str(exc)
                if "OAuth 2.0 User Context" in message:
                    self._reverse_timeline_enabled = False
                    logger.warning(
                        "Reverse timeline endpoint disabled for subsequent polls because app-only bearer "
                        "authentication is not supported: %s",
                        message,
                    )
                else:
                    logger.warning("Reverse timeline request failed for endpoint=%s: %s", timeline_endpoint, message)

        merged_by_id = {event.repost_tweet_id: event for event in profile_events}
        merged_by_id.update({event.repost_tweet_id: event for event in timeline_events})
        merged = sorted(merged_by_id.values(), key=lambda event: int(event.repost_tweet_id))
        logger.info(
            "Collected %s unique repost event(s) across profile+timeline endpoints",
            len(merged),
        )
        return merged

    def _collect_reposts_for_endpoint(self, endpoint_path: str, since_id: str | None = None) -> list[RepostEvent]:
        events: list[RepostEvent] = []
        tweets_seen = 0
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
            try:
                payload = get_json(
                    url,
                    headers=self._auth_headers(),
                    timeout=self.timeout,
                    retries=self.retries,
                    backoff_seconds=self.backoff_seconds,
                )
            except HTTPError as exc:
                raise RuntimeError(self._build_timeline_error_message(endpoint_path, exc)) from exc
            tweets = payload.get("data", [])
            if not tweets:
                logger.debug("No tweets returned endpoint=%s page=%s", endpoint_path, pages + 1)
                break
            tweets_seen += len(tweets)

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
        skipped_without_media = max(0, tweets_seen - len(events))
        if skipped_without_media:
            logger.info(
                "Skipped %s post(s) from endpoint=%s because they had no relayable media. "
                "This relay only emits events for posts/reposts that resolve to media.",
                skipped_without_media,
                endpoint_path,
            )
        logger.info("Collected %s repost event(s) from endpoint=%s", len(events), endpoint_path)

        return sorted(events, key=lambda e: int(e.repost_tweet_id))

    def _build_timeline_error_message(self, endpoint_path: str, exc: HTTPError) -> str:
        body_snippet = str(getattr(exc, "xdl_body_snippet", "") or "")
        if not body_snippet:
            try:
                if exc.fp and hasattr(exc.fp, "read"):
                    body_snippet = exc.fp.read(400).decode("utf-8", errors="replace")
            except Exception:
                body_snippet = ""
        base = (
            f"X timeline request failed for {endpoint_path} with status {exc.code} ({exc.reason}). "
            "Verify X_BEARER_TOKEN and X_USER_ID."
        )
        if exc.code == 403 and "unsupported-authentication" in body_snippet.lower():
            return (
                f"{base} This endpoint requires user-context auth (OAuth 2.0 User Context or OAuth 1.0a User Context), "
                "but the current token appears to be OAuth 2.0 app-only bearer auth. Use /users/:id/tweets only, "
                "or switch to a user-context access token."
            )
        if exc.code in {401, 403}:
            return (
                f"{base} The token can belong to a different X user than the monitored account, "
                "but your X app/project must have permission to read that target user's posts "
                "(and the target account must be accessible/public to your app)."
            )
        if exc.code == 404:
            return f"{base} Confirm the target user exists and X_USER_ID resolves to the correct account."
        if exc.code == 429:
            return (
                f"{base} X rate limit hit. Increase POLL_INTERVAL_SECONDS or reduce X_PAGE_SIZE/X_MAX_PAGES."
            )
        return base

    def _timeline_params(self, since_id: str | None = None, pagination_token: str | None = None) -> dict[str, str]:
        params = {
            "max_results": str(self.page_size),
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
        fetched_referenced_tweets: dict[str, tuple[dict, dict[str, dict]]] = {}

        for tweet in tweets:
            references = tweet.get("referenced_tweets", [])
            retweet_ref = self._find_repost_reference(references)
            is_repost = retweet_ref is not None
            if is_repost:
                referenced_id = retweet_ref.get("id", "")
                source_tweet = included_tweets.get(referenced_id)
                source_media_map = included_media
                if not source_tweet and referenced_id:
                    if referenced_id not in fetched_referenced_tweets:
                        fetched_referenced_tweets[referenced_id] = self._fetch_tweet_with_media(referenced_id)
                    fetched_tweet, fetched_media = fetched_referenced_tweets[referenced_id]
                    source_tweet = fetched_tweet
                    source_media_map = fetched_media
                if not source_tweet:
                    continue
            else:
                source_tweet = tweet
                source_media_map = included_media

            source_tweet, source_media_map = self._resolve_media_source(
                source_tweet=source_tweet,
                source_media_map=source_media_map,
                included_tweets=included_tweets,
                included_media=included_media,
                fetched_referenced_tweets=fetched_referenced_tweets,
            )

            media_keys = source_tweet.get("attachments", {}).get("media_keys", [])
            media = [
                self._convert_media(source_media_map.get(media_key), fallback_key=media_key)
                for media_key in media_keys
            ]
            media = [m for m in media if m is not None]

            if media:
                events.append(
                    RepostEvent(
                        repost_tweet_id=tweet["id"],
                        original_tweet_id=source_tweet.get("id", ""),
                        original_author_id=source_tweet.get("author_id", "unknown"),
                        repost_text=tweet.get("text", ""),
                        original_text=source_tweet.get("text", ""),
                        media=media,
                    )
                )
        return events

    def _find_repost_reference(self, references: list[dict]) -> dict | None:
        for ref in references:
            ref_type = str(ref.get("type", "")).lower()
            if ref_type in {"retweeted", "reposted"}:
                return ref
            # X response shapes can vary across endpoints/plans. Accept
            # any retweet/repost-like reference type to avoid dropping
            # genuine repost events when labels drift.
            if "retweet" in ref_type or "repost" in ref_type:
                return ref
        return None

    def _resolve_media_source(
        self,
        source_tweet: dict,
        source_media_map: dict[str, dict],
        included_tweets: dict[str, dict],
        included_media: dict[str, dict],
        fetched_referenced_tweets: dict[str, tuple[dict, dict[str, dict]]],
    ) -> tuple[dict, dict[str, dict]]:
        current_tweet = source_tweet
        current_media_map = source_media_map
        seen_ids: set[str] = set()

        # Walk a short chain of related references (repost/quote/reply) so
        # media attached to a parent/original post is not missed.
        for _ in range(5):
            media_keys = current_tweet.get("attachments", {}).get("media_keys", [])
            if media_keys:
                return current_tweet, current_media_map

            current_id = str(current_tweet.get("id", "") or "")
            if current_id:
                seen_ids.add(current_id)

            next_ref_id = self._find_next_reference_id(current_tweet.get("referenced_tweets", []), seen_ids)
            if not next_ref_id:
                break

            next_tweet = included_tweets.get(next_ref_id)
            next_media_map = included_media
            if not next_tweet:
                if next_ref_id not in fetched_referenced_tweets:
                    fetched_referenced_tweets[next_ref_id] = self._fetch_tweet_with_media(next_ref_id)
                fetched_tweet, fetched_media = fetched_referenced_tweets[next_ref_id]
                next_tweet = fetched_tweet
                next_media_map = fetched_media
            if not next_tweet:
                break

            current_tweet = next_tweet
            current_media_map = next_media_map

        return current_tweet, current_media_map

    def _find_next_reference_id(self, references: list[dict], seen_ids: set[str]) -> str:
        if not references:
            return ""
        priority = ("retweeted", "reposted", "quoted", "replied_to")
        for ref_type in priority:
            match = next((ref for ref in references if str(ref.get("type", "")).lower() == ref_type), None)
            ref_id = str((match or {}).get("id", "") or "")
            if ref_id and ref_id not in seen_ids:
                return ref_id
        for ref in references:
            ref_id = str(ref.get("id", "") or "")
            if ref_id and ref_id not in seen_ids:
                return ref_id
        return ""

    def _fetch_tweet_with_media(self, tweet_id: str) -> tuple[dict, dict[str, dict]]:
        params = {
            "tweet.fields": "text,author_id,attachments,referenced_tweets",
            "expansions": "attachments.media_keys,referenced_tweets.id,referenced_tweets.id.attachments.media_keys",
            "media.fields": "type,url,variants",
        }
        url = f"{self.API_BASE_URL}/tweets/{tweet_id}?{urlencode(params)}"
        try:
            payload = get_json(
                url,
                headers=self._auth_headers(),
                timeout=self.timeout,
                retries=self.retries,
                backoff_seconds=self.backoff_seconds,
            )
        except HTTPError:
            return {}, {}

        tweet = payload.get("data", {}) or {}
        media_map = {
            media["media_key"]: media
            for media in payload.get("includes", {}).get("media", [])
            if media.get("media_key")
        }
        return tweet, media_map

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
            if best_variant:
                url = best_variant.get("url")
            else:
                url = media_payload.get("url")
                if not url:
                    raw_variants = media_payload.get("video_info", {}).get("variants", []) or media_payload.get("variants", [])
                    first_variant = next((variant for variant in raw_variants if variant.get("url")), {})
                    url = first_variant.get("url")

        if not url or not media_key:
            return None

        return MediaItem(media_key=media_key, media_type=media_type, url=url)
