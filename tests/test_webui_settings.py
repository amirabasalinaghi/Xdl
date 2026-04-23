from __future__ import annotations

import logging
import os
import tempfile
import unittest
from unittest import mock

from xdl_relay.config import Settings
from xdl_relay.webui import (
    HTML_PAGE,
    InMemoryLogHandler,
    _env_file_path,
    _normalize_download_mode,
    _settings_payload,
    _to_float_or_default,
    _to_int_or_default,
    _write_env_file,
)


class TestWebUISettings(unittest.TestCase):
    def test_dashboard_numeric_fields_describe_limits(self) -> None:
        self.assertIn("Polling Interval (seconds, 1-3600)", HTML_PAGE)
        self.assertIn("HTTP Timeout (seconds, 1-300)", HTML_PAGE)
        self.assertIn("HTTP Retries (1-10)", HTML_PAGE)
        self.assertIn("Retry Backoff (seconds, 0-60)", HTML_PAGE)
        self.assertIn("Max Media Size (bytes, 1-52,428,800)", HTML_PAGE)
        self.assertIn("X API Max Pages (5-1000)", HTML_PAGE)
        self.assertIn("X API Page Size (5-100)", HTML_PAGE)
        self.assertIn("s.max_media_bytes || 52428800", HTML_PAGE)
        self.assertIn("Manual polling", HTML_PAGE)
        self.assertIn("card('Last updated', formatDateTime(o.last_update))", HTML_PAGE)
        self.assertIn("card('Profile posts scanned', o.total_profile_posts_seen || 0)", HTML_PAGE)
        self.assertIn("card('Reposts seen', o.total_reposts_seen || 0)", HTML_PAGE)
        self.assertIn("card('Delivery success rate', percentage(o.sent_events || 0, totalHandled))", HTML_PAGE)
        self.assertIn("document.getElementById('pill-last').textContent = formatDateTime(o.last_update);", HTML_PAGE)
        self.assertNotIn("card('Other referenced seen', o.total_other_reference_posts_seen || 0)", HTML_PAGE)
        self.assertNotIn("Index full profile media", HTML_PAGE)
        self.assertNotIn("Force refresh + retry unsent", HTML_PAGE)

    def test_normalize_download_mode(self) -> None:
        self.assertEqual(_normalize_download_mode("pic", "both"), "pic")
        self.assertEqual(_normalize_download_mode("video", "both"), "video")
        self.assertEqual(_normalize_download_mode("both", "pic"), "both")
        self.assertEqual(_normalize_download_mode("invalid", "pic"), "both")
        self.assertEqual(_normalize_download_mode(None, "video"), "video")

    def test_in_memory_log_handler_recent_and_level_filter(self) -> None:
        handler = InMemoryLogHandler(capacity=3)
        logger = logging.getLogger("test.webui")
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        self.addCleanup(lambda: logger.removeHandler(handler))

        logger.info("first message")
        logger.warning("second message")
        logger.error("third message")
        logger.info("fourth message")

        records = handler.recent(limit=2)
        self.assertEqual([record["message"] for record in records], ["fourth message", "third message"])

        warnings = handler.recent(limit=10, level="warning")
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["level"], "WARNING")

    def test_env_file_path_falls_back_to_dotenv_when_etc_unwritable(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("xdl_relay.webui.os.access", return_value=False):
                self.assertEqual(_env_file_path(), ".env")

    def test_write_env_file_creates_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_path = os.path.join(tmp_dir, "nested", "xdl.env")
            with mock.patch.dict(os.environ, {"RELAY_ENV_FILE": env_path}):
                _write_env_file(
                    Settings(
                        x_user_id="1",
                        x_bearer_token="bearer",
                        telegram_bot_token="bot",
                        telegram_chat_id="chat",
                    )
                )
            self.assertTrue(os.path.exists(env_path))


    def test_numeric_parsers_apply_defaults_and_bounds(self) -> None:
        self.assertEqual(_to_int_or_default("10", 3), 10)
        self.assertEqual(_to_int_or_default("0", 3), 1)
        self.assertEqual(_to_int_or_default("bad", 3), 3)

        self.assertEqual(_to_float_or_default("3.5", 1.0), 3.5)
        self.assertEqual(_to_float_or_default("-1", 1.0), 0.0)
        self.assertEqual(_to_float_or_default(None, 1.25), 1.25)

    def test_settings_payload_contains_dashboard_fields(self) -> None:
        settings = Settings(
            x_user_id="123",
            x_bearer_token="bearer",
            telegram_bot_token="bot",
            telegram_chat_id="-100",
            media_download_mode="video",
        )

        payload = _settings_payload(settings)

        self.assertEqual(payload["x_user_id"], "123")
        self.assertEqual(payload["x_bearer_token"], "bearer")
        self.assertEqual(payload["telegram_bot_token"], "bot")
        self.assertEqual(payload["telegram_chat_id"], "-100")
        self.assertEqual(payload["media_download_mode"], "video")
        self.assertEqual(payload["poll_interval_seconds"], 15)
        self.assertEqual(payload["http_timeout_seconds"], 60)
        self.assertEqual(payload["http_retries"], 5)
        self.assertEqual(payload["http_backoff_seconds"], 2.0)
        self.assertEqual(payload["max_media_bytes"], 200 * 1024 * 1024)
        self.assertEqual(payload["x_max_pages"], 100)
        self.assertEqual(payload["x_page_size"], 100)
        self.assertEqual(payload["X_USER_ID"], "123")


if __name__ == "__main__":
    unittest.main()
