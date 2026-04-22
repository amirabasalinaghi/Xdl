from __future__ import annotations

import logging
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


def download_file(url: str, destination: Path, timeout: int = 60, max_bytes: int | None = None) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = Request(
        url,
        method="GET",
        headers={
            # X CDN/media endpoints increasingly reject anonymous default clients.
            "User-Agent": "Mozilla/5.0 (compatible; xdl-relay/1.0)",
            "Accept": "*/*",
        },
    )

    total = 0
    try:
        with urlopen(request, timeout=timeout) as response, destination.open("wb") as handle:
            content_length = response.headers.get("Content-Length")
            if max_bytes is not None and content_length and content_length.isdigit() and int(content_length) > max_bytes:
                raise ValueError(f"Media file too large: {content_length} bytes exceeds max {max_bytes}")

            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if max_bytes is not None and total > max_bytes:
                    raise ValueError(f"Media download exceeded max size of {max_bytes} bytes")
                handle.write(chunk)
    except HTTPError as exc:
        body_snippet = ""
        try:
            body_snippet = exc.read(400).decode("utf-8", errors="replace")
        except Exception:
            body_snippet = "<unreadable>"
        logger.error(
            "Media download failed with HTTP %s for url=%s destination=%s reason=%s body=%s",
            exc.code,
            url,
            destination,
            exc.reason,
            body_snippet,
        )
        raise

    return destination
