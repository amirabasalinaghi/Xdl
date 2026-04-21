from __future__ import annotations

from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json


def get_json(url: str, headers: dict[str, str] | None = None) -> dict:
    request = Request(url, headers=headers or {}, method="GET")
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def post_form_json(url: str, form_data: dict[str, str]) -> dict:
    data = urlencode(form_data).encode("utf-8")
    request = Request(url, data=data, method="POST")
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))
