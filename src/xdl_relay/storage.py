from __future__ import annotations

from pathlib import Path
from urllib.request import Request, urlopen


def download_file(url: str, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = Request(url, method="GET")
    with urlopen(request, timeout=60) as response:
        destination.write_bytes(response.read())
    return destination
