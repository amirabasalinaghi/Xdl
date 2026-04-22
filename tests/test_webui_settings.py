from __future__ import annotations

import unittest

from xdl_relay.webui import _normalize_download_mode


class TestWebUISettings(unittest.TestCase):
    def test_normalize_download_mode(self) -> None:
        self.assertEqual(_normalize_download_mode("pic", "both"), "pic")
        self.assertEqual(_normalize_download_mode("video", "both"), "video")
        self.assertEqual(_normalize_download_mode("both", "pic"), "both")
        self.assertEqual(_normalize_download_mode("invalid", "pic"), "both")
        self.assertEqual(_normalize_download_mode(None, "video"), "video")


if __name__ == "__main__":
    unittest.main()
