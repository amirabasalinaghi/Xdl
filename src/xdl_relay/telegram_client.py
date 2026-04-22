from __future__ import annotations

import json
import logging
import mimetypes
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


class TelegramClient:
    def __init__(self, bot_token: str) -> None:
        self.base_url = f"https://api.telegram.org/bot{bot_token}"

    def send_media(self, chat_id: str, files: list[Path], caption: str | None = None) -> list[int]:
        if len(files) == 1:
            return [self._send_single(chat_id, files[0], caption)]
        return self._send_group(chat_id, files, caption)

    def send_message(self, chat_id: str, text: str) -> int:
        payload = self._multipart_request(
            f"{self.base_url}/sendMessage",
            fields={"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"},
            files={},
        )
        return int(payload["result"]["message_id"])

    def _send_single(self, chat_id: str, file_path: Path, caption: str | None = None) -> int:
        endpoint = "sendVideo" if self._is_video(file_path) else "sendPhoto"
        media_field = "video" if endpoint == "sendVideo" else "photo"
        fields = {"chat_id": chat_id}
        if caption:
            fields["caption"] = caption[:1024]
        payload = self._multipart_request(
            f"{self.base_url}/{endpoint}",
            fields=fields,
            files={media_field: file_path},
        )
        return int(payload["result"]["message_id"])

    def _send_group(self, chat_id: str, files: list[Path], caption: str | None = None) -> list[int]:
        media = []
        attachments: dict[str, Path] = {}
        for idx, file in enumerate(files):
            attach_name = f"file{idx}"
            item = {"type": "video" if self._is_video(file) else "photo", "media": f"attach://{attach_name}"}
            if idx == 0 and caption:
                item["caption"] = caption[:1024]
            media.append(item)
            attachments[attach_name] = file

        payload = self._multipart_request(
            f"{self.base_url}/sendMediaGroup",
            fields={"chat_id": chat_id, "media": json.dumps(media)},
            files=attachments,
        )
        return [int(m["message_id"]) for m in payload["result"]]

    def _multipart_request(self, url: str, fields: dict[str, str], files: dict[str, Path]) -> dict:
        boundary = f"----xdl-{uuid.uuid4().hex}"
        body = bytearray()

        for name, value in fields.items():
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            body.extend(str(value).encode())
            body.extend(b"\r\n")

        for name, path in files.items():
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            body.extend(f"--{boundary}\r\n".encode())
            body.extend(
                f'Content-Disposition: form-data; name="{name}"; filename="{path.name}"\r\n'.encode()
            )
            body.extend(f"Content-Type: {mime}\r\n\r\n".encode())
            body.extend(path.read_bytes())
            body.extend(b"\r\n")

        body.extend(f"--{boundary}--\r\n".encode())

        request = Request(
            url,
            data=bytes(body),
            method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        try:
            with urlopen(request, timeout=60) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body_snippet = ""
            try:
                body_snippet = exc.read(400).decode("utf-8", errors="replace")
            except Exception:
                body_snippet = "<unreadable>"
            logger.error(
                "Telegram API request failed: status=%s endpoint=%s reason=%s fields=%s files=%s body=%s",
                exc.code,
                url.rsplit("/", 1)[-1],
                exc.reason,
                sorted(fields.keys()),
                sorted(files.keys()),
                body_snippet,
            )
            raise

        if not payload.get("ok", False):
            logger.error(
                "Telegram API returned ok=false endpoint=%s description=%s error_code=%s",
                url.rsplit("/", 1)[-1],
                payload.get("description"),
                payload.get("error_code"),
            )
            raise RuntimeError(
                f"Telegram API error {payload.get('error_code')}: {payload.get('description', 'unknown error')}"
            )

        return payload

    @staticmethod
    def _is_video(path: Path) -> bool:
        return path.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"}
