from __future__ import annotations

import os
import unittest
from unittest import mock

from xdl_relay.config import Settings


class TestSettings(unittest.TestCase):
    def test_from_env_clamps_x_pagination_bounds(self) -> None:
        env = {
            "X_USER_ID": "1",
            "X_BEARER_TOKEN": "token",
            "TELEGRAM_BOT_TOKEN": "bot",
            "TELEGRAM_CHAT_ID": "chat",
            "X_MAX_PAGES": "1",
            "X_PAGE_SIZE": "1000",
        }

        with mock.patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()

        self.assertEqual(settings.x_max_pages, 5)
        self.assertEqual(settings.x_page_size, 100)


if __name__ == "__main__":
    unittest.main()
