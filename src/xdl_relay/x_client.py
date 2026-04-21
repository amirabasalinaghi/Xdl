from __future__ import annotations

from urllib.parse import parse_qs, urlparse, urlencode

from xdl_relay.http_utils import get_json
from xdl_relay.models import MediaItem, RepostEvent
from xdl_relay.x_auth import OAuthTokenStore, XOAuthPKCE


class XClient:
    API_BASE_URL = "https://api.x.com/2"

    def __init__(
        self,
        timeout: int = 30,
        retries: int = 3,
        backoff_seconds: float = 1.0,
        max_pages: int = 5,
        client_id: str = "",
        redirect_uri: str = "https://localhost/callback",
        token_path: str = "x_oauth_token.json",
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.backoff_seconds = backoff_seconds
        self.max_pages = max_pages
        self.client_id = client_id
        self.redirect_uri = redirect_uri
        self.token_store = OAuthTokenStore(token_path)
        self.oauth = XOAuthPKCE(client_id=client_id, redirect_uri=redirect_uri)

    def interactive_login(self) -> None:
        auth_url, expected_state, verifier = self.oauth.create_authorization_request()
        print("\nOpen this URL in your browser and approve access:\n")
        print(auth_url)
        print("\nAfter approval, paste the full redirected URL here.")
        callback_url = input("Redirect URL: ").strip()
        parsed = urlparse(callback_url)
        query = parse_qs(parsed.query)
        code = query.get("code", [None])[0]
        state = query.get("state", [None])[0]
        if not code:
            raise RuntimeError("Login failed: callback URL did not include an authorization code.")
        if state != expected_state:
            raise RuntimeError("Login failed: OAuth state mismatch.")
        token = self.oauth.exchange_code(code=code, code_verifier=verifier)
        self.token_store.save(token)

    def get_new_reposts(self, user_id: str, since_id: str | None = None) -> list[RepostEvent]:
        events: list[RepostEvent] = []
        max_id: str | None = None
        pages = 0

        while pages < self.max_pages:
            params = {
                "max_results": "100",
                "exclude": "replies",
                "tweet.fields": "text,referenced_tweets,attachments",
                "expansions": "referenced_tweets.id,referenced_tweets.id.author_id,referenced_tweets.id.attachments.media_keys",
                "media.fields": "type,url,variants",
            }
            if since_id:
                params["since_id"] = since_id
            if max_id:
                params["pagination_token"] = max_id

            url = f"{self.API_BASE_URL}/users/{user_id}/tweets?{urlencode(params)}"
            payload = get_json(
                url,
                headers=self._oauth_headers(),
                timeout=self.timeout,
                retries=self.retries,
                backoff_seconds=self.backoff_seconds,
            )
            tweets = payload.get("data", [])
            if not tweets:
                break

            included_tweets = {t["id"]: t for t in payload.get("includes", {}).get("tweets", []) if t.get("id")}
            included_media = {
                m["media_key"]: m for m in payload.get("includes", {}).get("media", []) if m.get("media_key")
            }

            for tweet in tweets:
                references = tweet.get("referenced_tweets", [])
                retweet_ref = next((ref for ref in references if ref.get("type") == "retweeted"), None)
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

            pages += 1
            max_id = payload.get("meta", {}).get("next_token")
            if not max_id:
                break

        return sorted(events, key=lambda e: int(e.repost_tweet_id))

    def _oauth_headers(self) -> dict[str, str]:
        token = self.token_store.load()
        if token is None:
            raise RuntimeError("X user token is missing. Run login flow first.")
        if token.is_expired():
            token = self.oauth.refresh(token.refresh_token)
            self.token_store.save(token)
        return {
            "Authorization": f"Bearer {token.access_token}",
            "User-Agent": "Mozilla/5.0",
        }

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
