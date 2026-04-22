from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from xdl_relay.config import Settings
from xdl_relay.db import RelayDB
from xdl_relay.enhancements import (
    build_caption_from_template,
    build_repost_permalink,
    compute_poll_jitter,
    detect_stuck_events,
    estimate_media_send_strategy,
    extract_best_media_variant,
    hash_media_file,
    is_duplicate_media_hash,
    next_retry_delay,
    parse_retry_after_from_telegram,
    record_media_hash,
    redact_sensitive_config,
    requeue_stuck_events,
    sanitize_caption_for_telegram,
    should_skip_repost_by_age,
    split_caption_chunks,
    summarize_cycle_metrics,
    validate_runtime_config,
)


class _FakeTelegram:
    def __init__(self) -> None:
        self.calls = 0

    def send_media(self, chat_id: str, files: list[Path], caption: str | None = None) -> list[int]:
        self.calls += 1
        if self.calls == 1 and len(files) > 1:
            raise RuntimeError("fail group")
        return [100 + self.calls]


class TestEnhancements(unittest.TestCase):
    def test_core_helpers(self) -> None:
        jittered = compute_poll_jitter(10, 0.1)
        self.assertGreaterEqual(jittered, 9)
        self.assertLessEqual(jittered, 11)

        old = datetime.now(timezone.utc) - timedelta(minutes=15)
        self.assertTrue(should_skip_repost_by_age(old, max_age_minutes=10))

        variant = extract_best_media_variant(
            {
                "variants": [
                    {"content_type": "video/mp4", "bitrate": 10, "url": "a"},
                    {"content_type": "video/mp4", "bitrate": 20, "url": "b"},
                ]
            }
        )
        self.assertEqual(variant["url"], "b")

        cap = build_caption_from_template({"author": "me", "url": "u", "text": "t"}, "{author} {url} {text}")
        self.assertEqual(cap, "me u t")

        escaped = sanitize_caption_for_telegram("hey_[x]")
        self.assertIn("\\_", escaped)

        chunks = split_caption_chunks("a " * 700, max_len=128)
        self.assertTrue(all(len(c) <= 128 for c in chunks))

        self.assertEqual(parse_retry_after_from_telegram({"parameters": {"retry_after": 8}}), 8)
        self.assertIsNone(parse_retry_after_from_telegram({}))

        self.assertGreaterEqual(next_retry_delay(3, 1, 10, jitter=False), 4)

        strategy = estimate_media_send_strategy([{"type": "photo"}, {"type": "photo"}])
        self.assertEqual(strategy, "album")

        metrics = summarize_cycle_metrics([{"status": "sent", "latency_seconds": 1.5}, {"status": "failed"}])
        self.assertEqual(metrics["processed_total"], 2)
        self.assertEqual(metrics["sent_total"], 1)

        self.assertEqual(build_repost_permalink("42"), "https://x.com/i/web/status/42")

    def test_db_hash_and_stale_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = RelayDB(str(Path(tmp) / "relay.db"))
            p = Path(tmp) / "f.bin"
            p.write_bytes(b"hello")
            digest = hash_media_file(p)
            self.assertTrue(digest)
            self.assertFalse(is_duplicate_media_hash(db, digest))
            record_media_hash(db, "r1", digest, str(p))
            self.assertTrue(is_duplicate_media_hash(db, digest))

            db.create_repost_event("200", "100")
            # backdate for stale selection
            with db._connect() as conn:  # noqa: SLF001 - test-only setup
                conn.execute(
                    "UPDATE repost_events SET updated_at = datetime('now', '-30 minutes') WHERE repost_tweet_id = '200'"
                )
            stale = detect_stuck_events(db, 5)
            self.assertEqual(len(stale), 1)
            updated = requeue_stuck_events(db, 5)
            self.assertEqual(updated, 1)

    def test_config_and_redaction(self) -> None:
        bad = Settings(
            x_user_id="",
            x_bearer_token="",
            telegram_bot_token="",
            telegram_chat_id="",
            poll_interval_seconds=0,
        )
        errs = validate_runtime_config(bad)
        self.assertGreaterEqual(len(errs), 4)

        redacted = redact_sensitive_config({"telegram_bot_token": "123456", "x_user_id": "abc"})
        self.assertEqual(redacted["telegram_bot_token"], "1234...")
        self.assertEqual(redacted["x_user_id"], "abc")


if __name__ == "__main__":
    unittest.main()
