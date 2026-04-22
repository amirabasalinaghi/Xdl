from __future__ import annotations

import logging
import unittest

from xdl_relay.webui import InMemoryLogHandler, _normalize_download_mode


class TestWebUISettings(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
