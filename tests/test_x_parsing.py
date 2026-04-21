from __future__ import annotations

import unittest

from xdl_relay.x_client import XClient


class TestXParsing(unittest.TestCase):
    def test_convert_media_selects_best_variant(self) -> None:
        client = XClient()
        media = {
            "id_str": "3_1",
            "type": "video",
            "video_info": {
                "variants": [
                    {"content_type": "video/mp4", "bitrate": 256000, "url": "http://low.mp4"},
                    {"content_type": "video/mp4", "bitrate": 832000, "url": "http://high.mp4"},
                ]
            },
        }

        converted = client._convert_media(media)
        self.assertIsNotNone(converted)
        self.assertEqual(converted.url, "http://high.mp4")


if __name__ == "__main__":
    unittest.main()
