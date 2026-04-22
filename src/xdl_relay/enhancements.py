from __future__ import annotations

import hashlib
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from xdl_relay.db import RelayDB

TELEGRAM_CAPTION_LIMIT = 1024


def compute_poll_jitter(base_interval: int, spread_pct: float) -> float:
    """Return a jittered poll interval bounded by spread percentage."""
    spread = max(0.0, min(1.0, spread_pct))
    delta = base_interval * spread
    return max(0.0, random.uniform(base_interval - delta, base_interval + delta))


def should_skip_repost_by_age(created_at: datetime, max_age_minutes: int) -> bool:
    """True when tweet age exceeds configured threshold."""
    if max_age_minutes <= 0:
        return False
    now = datetime.now(timezone.utc)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return (now - created_at).total_seconds() > max_age_minutes * 60


def extract_best_media_variant(media_obj: dict[str, Any]) -> dict[str, Any]:
    """Pick the best mp4 variant from X media payload according to bitrate."""
    variants = media_obj.get("video_info", {}).get("variants", []) or media_obj.get("variants", [])
    mp4_variants = [v for v in variants if v.get("content_type") == "video/mp4" and v.get("url")]
    if not mp4_variants:
        return {}
    return max(mp4_variants, key=lambda v: int(v.get("bitrate", v.get("bit_rate", 0)) or 0))


def build_caption_from_template(tweet: dict[str, Any], template: str) -> str:
    """Build a caption using safe placeholder replacement."""
    values = {
        "author": str(tweet.get("author", "") or ""),
        "url": str(tweet.get("url", "") or ""),
        "text": str(tweet.get("text", "") or ""),
        "tweet_id": str(tweet.get("tweet_id", "") or ""),
        "repost_id": str(tweet.get("repost_id", "") or ""),
        "hashtags": " ".join(tweet.get("hashtags", []) or []),
    }

    caption = template
    for key, value in values.items():
        caption = caption.replace(f"{{{key}}}", value)
    return caption


def sanitize_caption_for_telegram(text: str, mode: str = "MarkdownV2") -> str:
    """Escape Telegram-sensitive characters for selected parse mode."""
    if mode.upper() == "HTML":
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    # MarkdownV2 escaping per Telegram formatting rules.
    return re.sub(r"([_\*\[\]\(\)~`>#+\-=|{}.!\\])", r"\\\1", text)


def split_caption_chunks(text: str, max_len: int = TELEGRAM_CAPTION_LIMIT) -> list[str]:
    """Split text into Telegram-safe chunks preserving words where possible."""
    if max_len <= 0:
        return [text]
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    current = ""
    for word in text.split():
        tentative = f"{current} {word}".strip()
        if len(tentative) <= max_len:
            current = tentative
            continue
        if current:
            chunks.append(current)
        if len(word) <= max_len:
            current = word
        else:
            for i in range(0, len(word), max_len):
                chunks.append(word[i : i + max_len])
            current = ""
    if current:
        chunks.append(current)
    return chunks


def hash_media_file(path: Path, algo: str = "sha256") -> str:
    h = hashlib.new(algo)
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def is_duplicate_media_hash(db: RelayDB, media_hash: str) -> bool:
    return db.media_hash_exists(media_hash)


def record_media_hash(db: RelayDB, repost_id: str, media_hash: str, file_path: str) -> None:
    db.record_media_hash(repost_id=repost_id, media_hash=media_hash, file_path=file_path)


def estimate_media_send_strategy(media_items: list[dict[str, Any]]) -> str:
    if len(media_items) <= 1:
        return "single"
    has_video = any((m.get("type") or "").lower() in {"video", "animation"} for m in media_items)
    return "single" if has_video and len(media_items) > 8 else "album"


def send_with_fallback_strategy(tg_client: Any, media_items: list[Path], chat_id: str, caption: str | None) -> dict[str, Any]:
    try:
        ids = tg_client.send_media(chat_id, media_items, caption=caption)
        return {"strategy": "primary", "message_ids": ids, "fallback_used": False}
    except Exception:
        message_ids: list[int] = []
        for idx, item in enumerate(media_items):
            sent = tg_client.send_media(chat_id, [item], caption=caption if idx == 0 else None)
            message_ids.extend(sent)
        return {"strategy": "fallback-single", "message_ids": message_ids, "fallback_used": True}


def parse_retry_after_from_telegram(error_payload: dict[str, Any]) -> int | None:
    params = error_payload.get("parameters", {})
    retry_after = params.get("retry_after")
    if retry_after is None:
        return None
    try:
        return max(0, int(retry_after))
    except (TypeError, ValueError):
        return None


def next_retry_delay(attempt: int, base: float, cap: float, jitter: bool = True) -> float:
    delay = min(cap, base * (2 ** max(0, attempt - 1)))
    if not jitter:
        return max(0.0, delay)
    return max(0.0, random.uniform(delay * 0.5, delay * 1.5))


def detect_stuck_events(db: RelayDB, stale_after_minutes: int) -> list[dict[str, str | None]]:
    return db.list_stale_pending_events(stale_after_minutes=stale_after_minutes)


def requeue_stuck_events(db: RelayDB, stale_after_minutes: int) -> int:
    stale = detect_stuck_events(db, stale_after_minutes=stale_after_minutes)
    return db.requeue_events([row["repost_tweet_id"] for row in stale if row.get("repost_tweet_id")])


def summarize_cycle_metrics(events: list[dict[str, Any]]) -> dict[str, float | int]:
    status_counts = {"sent": 0, "failed": 0, "pending": 0, "skipped": 0}
    total_latency = 0.0
    latencies = 0
    for event in events:
        status = str(event.get("status", "pending"))
        if status not in status_counts:
            status = "skipped"
        status_counts[status] += 1
        if "latency_seconds" in event:
            total_latency += float(event["latency_seconds"])
            latencies += 1

    avg_latency = (total_latency / latencies) if latencies else 0.0
    return {
        "processed_total": len(events),
        "sent_total": status_counts["sent"],
        "failed_total": status_counts["failed"],
        "pending_total": status_counts["pending"],
        "skipped_total": status_counts["skipped"],
        "avg_latency_seconds": round(avg_latency, 4),
    }


def emit_metrics_snapshot(metrics: dict[str, Any], sink: str = "log") -> None:
    # Keep this dependency-free by supporting only no-op/log-style output.
    if sink == "log":
        # Lazy import to avoid forcing global logging config.
        import logging

        logging.getLogger(__name__).info("cycle_metrics=%s", metrics)


def validate_runtime_config(config: Any) -> list[str]:
    errors: list[str] = []
    if not getattr(config, "x_user_id", ""):
        errors.append("X_USER_ID is required")
    if not getattr(config, "x_bearer_token", ""):
        errors.append("X_BEARER_TOKEN is required")
    if not getattr(config, "telegram_bot_token", ""):
        errors.append("TELEGRAM_BOT_TOKEN is required")
    if not getattr(config, "telegram_chat_id", ""):
        errors.append("TELEGRAM_CHAT_ID is required")
    if int(getattr(config, "poll_interval_seconds", 1) or 1) <= 0:
        errors.append("POLL_INTERVAL_SECONDS must be > 0")
    return errors


def redact_sensitive_config(config_dict: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(config_dict)
    for key in list(redacted.keys()):
        upper = key.upper()
        if any(token in upper for token in ["TOKEN", "SECRET", "PASSWORD", "KEY"]):
            val = str(redacted[key])
            redacted[key] = f"{val[:4]}..." if val else "***"
    return redacted


def build_repost_permalink(tweet_id: str, user_handle: str | None = None) -> str:
    if user_handle:
        return f"https://x.com/{user_handle.lstrip('@')}/status/{tweet_id}"
    return f"https://x.com/i/web/status/{tweet_id}"


def sleep_with_retry_after(error_payload: dict[str, Any]) -> int:
    """Small helper that uses retry_after from Telegram payload when present."""
    retry_after = parse_retry_after_from_telegram(error_payload) or 0
    if retry_after > 0:
        time.sleep(retry_after)
    return retry_after
