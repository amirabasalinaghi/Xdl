from __future__ import annotations

import tempfile
import unittest
from http.client import IncompleteRead
from pathlib import Path
from unittest import mock
from urllib.error import URLError

from xdl_relay.storage import download_file


class _FakeResponse:
    def __init__(self, chunks: list[bytes | Exception], headers: dict[str, str] | None = None) -> None:
        self._chunks = list(chunks)
        self.headers = headers or {}

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self, _size: int = -1) -> bytes:
        if not self._chunks:
            return b""
        chunk = self._chunks.pop(0)
        if isinstance(chunk, Exception):
            raise chunk
        return chunk


class TestStorageDownload(unittest.TestCase):
    def test_download_file_retries_on_transient_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "file.jpg"
            side_effects = [
                URLError("temporary network issue"),
                _FakeResponse([b"abc", b"def"]),
            ]

            with mock.patch("xdl_relay.storage.urlopen", side_effect=side_effects), mock.patch(
                "xdl_relay.storage.time.sleep"
            ) as sleep_mock:
                out = download_file(
                    "https://example.com/a.jpg",
                    dest,
                    retries=3,
                    backoff_seconds=0.01,
                )

            self.assertEqual(out, dest)
            self.assertEqual(dest.read_bytes(), b"abcdef")
            sleep_mock.assert_called_once()

    def test_download_file_removes_partial_file_after_failed_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "file.jpg"
            part = Path(f"{dest}.part")
            side_effects = [
                _FakeResponse([b"partial-data", IncompleteRead(b"partial-data")], headers={"Content-Length": "120"}),
                _FakeResponse([b"ok-data"]),
            ]

            with mock.patch("xdl_relay.storage.urlopen", side_effect=side_effects), mock.patch(
                "xdl_relay.storage.time.sleep"
            ):
                out = download_file(
                    "https://example.com/a.jpg",
                    dest,
                    retries=2,
                    backoff_seconds=0.01,
                )

            self.assertEqual(out, dest)
            self.assertEqual(dest.read_bytes(), b"ok-data")
            self.assertFalse(part.exists())
