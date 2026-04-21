from __future__ import annotations

from pathlib import Path
from urllib.request import Request, urlopen


def download_file(url: str, destination: Path, timeout: int = 60, max_bytes: int | None = None) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = Request(url, method="GET")

    total = 0
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

    return destination
