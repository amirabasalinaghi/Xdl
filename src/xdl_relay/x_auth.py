from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

from xdl_relay.http_utils import post_form_json


AUTH_BASE_URL = "https://x.com/i/oauth2/authorize"
TOKEN_URL = "https://api.x.com/2/oauth2/token"
DEFAULT_SCOPES = ("tweet.read", "users.read", "offline.access")


@dataclass
class OAuthToken:
    access_token: str
    refresh_token: str
    expires_at: int
    token_type: str = "bearer"
    scope: str = ""

    def is_expired(self, skew_seconds: int = 60) -> bool:
        return int(time.time()) + skew_seconds >= self.expires_at

    def to_json(self) -> dict[str, str | int]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "token_type": self.token_type,
            "scope": self.scope,
        }

    @staticmethod
    def from_json(payload: dict[str, str | int]) -> "OAuthToken":
        return OAuthToken(
            access_token=str(payload["access_token"]),
            refresh_token=str(payload["refresh_token"]),
            expires_at=int(payload["expires_at"]),
            token_type=str(payload.get("token_type", "bearer")),
            scope=str(payload.get("scope", "")),
        )


class XOAuthPKCE:
    def __init__(self, client_id: str, redirect_uri: str, scopes: tuple[str, ...] = DEFAULT_SCOPES) -> None:
        self.client_id = client_id
        self.redirect_uri = redirect_uri
        self.scopes = scopes

    def create_authorization_request(self) -> tuple[str, str, str]:
        code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode("ascii").rstrip("=")
        challenge_digest = hashlib.sha256(code_verifier.encode("utf-8")).digest()
        code_challenge = base64.urlsafe_b64encode(challenge_digest).decode("ascii").rstrip("=")
        state = secrets.token_urlsafe(24)
        query = urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": self.client_id,
                "redirect_uri": self.redirect_uri,
                "scope": " ".join(self.scopes),
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{AUTH_BASE_URL}?{query}", state, code_verifier

    def exchange_code(self, code: str, code_verifier: str) -> OAuthToken:
        response = post_form_json(
            TOKEN_URL,
            form_data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": self.client_id,
                "redirect_uri": self.redirect_uri,
                "code_verifier": code_verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        return self._token_from_response(response)

    def refresh(self, refresh_token: str) -> OAuthToken:
        response = post_form_json(
            TOKEN_URL,
            form_data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self.client_id,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if "refresh_token" not in response:
            response["refresh_token"] = refresh_token
        return self._token_from_response(response)

    def _token_from_response(self, response: dict) -> OAuthToken:
        expires_in = int(response.get("expires_in", 7200))
        return OAuthToken(
            access_token=str(response["access_token"]),
            refresh_token=str(response["refresh_token"]),
            expires_at=int(time.time()) + expires_in,
            token_type=str(response.get("token_type", "bearer")),
            scope=str(response.get("scope", "")),
        )


class OAuthTokenStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def load(self) -> OAuthToken | None:
        if not self.path.exists():
            return None
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        return OAuthToken.from_json(payload)

    def save(self, token: OAuthToken) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(token.to_json(), indent=2), encoding="utf-8")
