from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MediaItem:
    media_key: str
    media_type: str
    url: str


@dataclass(frozen=True)
class RepostEvent:
    repost_tweet_id: str
    original_tweet_id: str
    original_author_id: str
    repost_text: str
    original_text: str
    media: list[MediaItem]
