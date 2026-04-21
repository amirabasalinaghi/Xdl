from __future__ import annotations

import unittest

from xdl_relay.x_client import XClient


class TestXParsing(unittest.TestCase):
    def test_convert_media_selects_best_variant(self) -> None:
        client = XClient("token")
        media = {
            "media_key": "3_1",
            "type": "video",
            "variants": [
                {"content_type": "video/mp4", "bit_rate": 256000, "url": "http://low.mp4"},
                {"content_type": "video/mp4", "bit_rate": 832000, "url": "http://high.mp4"},
            ],
        }

        converted = client._convert_media(media, "3_1")
        self.assertIsNotNone(converted)
        self.assertEqual(converted.url, "http://high.mp4")


if __name__ == "__main__":
    unittest.main()
