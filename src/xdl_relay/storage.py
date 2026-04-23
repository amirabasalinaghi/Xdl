from __future__ import annotations

import logging
import socket
import time
from http.client import IncompleteRead
from pathlib import Path
from urllib.error import ContentTooShortError, HTTPError, URLError
from urllib.request import Request, urlopen

from xdl_relay.http_utils import _parse_retry_after, _retry_delay_seconds

logger = logging.getLogger(__name__)


def _is_retryable_status(status_code: int) -> bool:
    return status_code == 429 or 500 <= status_code <= 599 or status_code in {408, 425}


def _cleanup_partial_file(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        logger.warning("Failed to remove partial download file path=%s", path, exc_info=True)


def download_file(
    url: str,
    destination: Path,
    timeout: int = 60,
    max_bytes: int | None = None,
    retries: int = 3,
    backoff_seconds: float = 1.0,
) -> Path:
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

    temp_path = destination.with_suffix(f"{destination.suffix}.part")
    for attempt in range(1, max(retries, 1) + 1):
        total = 0
        _cleanup_partial_file(temp_path)
        try:
            with urlopen(request, timeout=timeout) as response, temp_path.open("wb") as handle:
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
            temp_path.replace(destination)
            return destination
        except ValueError:
            _cleanup_partial_file(temp_path)
            raise
        except HTTPError as exc:
            _cleanup_partial_file(temp_path)
            body_snippet = ""
            try:
                body_snippet = exc.read(400).decode("utf-8", errors="replace")
            except Exception:
                body_snippet = "<unreadable>"
            retryable = _is_retryable_status(exc.code)
            logger.warning(
                "Media download HTTPError status=%s attempt=%s/%s retryable=%s url=%s destination=%s reason=%s body=%s",
                exc.code,
                attempt,
                retries,
                retryable,
                url,
                destination,
                exc.reason,
                body_snippet,
            )
            if retryable and attempt < retries:
                retry_after = _parse_retry_after(exc.headers.get("Retry-After"))
                time.sleep(retry_after if retry_after is not None else _retry_delay_seconds(attempt, backoff_seconds))
                continue
            raise
        except (URLError, TimeoutError, socket.timeout, IncompleteRead, ContentTooShortError) as exc:
            _cleanup_partial_file(temp_path)
            logger.warning(
                "Media download transient error attempt=%s/%s url=%s destination=%s error=%s",
                attempt,
                retries,
                url,
                destination,
                exc,
            )
            if attempt < retries:
                time.sleep(_retry_delay_seconds(attempt, backoff_seconds))
                continue
            raise

    raise RuntimeError("download_file failed without an explicit error")
