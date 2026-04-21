from __future__ import annotations

import json
import time
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def _retry_delay_seconds(attempt: int, base_delay: float) -> float:
    return base_delay * (2 ** max(attempt - 1, 0))


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    if value.isdigit():
        return float(value)
    try:
        dt = parsedate_to_datetime(value)
        return max(0.0, dt.timestamp() - time.time())
    except Exception:
        return None


def get_json(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
    retries: int = 3,
    backoff_seconds: float = 1.0,
) -> dict:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        request = Request(url, headers=headers or {}, method="GET")
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            last_error = exc
            if exc.code == 429 and attempt < retries:
                retry_after = _parse_retry_after(exc.headers.get("Retry-After"))
                time.sleep(retry_after if retry_after is not None else _retry_delay_seconds(attempt, backoff_seconds))
                continue
            if 500 <= exc.code <= 599 and attempt < retries:
                time.sleep(_retry_delay_seconds(attempt, backoff_seconds))
                continue
            raise
        except URLError as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(_retry_delay_seconds(attempt, backoff_seconds))
                continue
            raise

    if last_error:
        raise last_error
    raise RuntimeError("get_json failed without an explicit error")


def post_form_json(
    url: str,
    form_data: dict[str, str],
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> dict:
    data = urlencode(form_data).encode("utf-8")
    request = Request(url, data=data, headers=headers or {}, method="POST")
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))
