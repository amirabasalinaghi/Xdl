from __future__ import annotations

import json
import mimetypes
import uuid
from pathlib import Path
from urllib.request import Request, urlopen


class TelegramClient:
    def __init__(self, bot_token: str) -> None:
        self.base_url = f"https://api.telegram.org/bot{bot_token}"

    def send_media(self, chat_id: str, files: list[Path]) -> list[int]:
        if len(files) == 1:
            return [self._send_single(chat_id, files[0])]
        return self._send_group(chat_id, files)

    def _send_single(self, chat_id: str, file_path: Path) -> int:
        endpoint = "sendVideo" if self._is_video(file_path) else "sendPhoto"
        media_field = "video" if endpoint == "sendVideo" else "photo"
        payload = self._multipart_request(
            f"{self.base_url}/{endpoint}",
            fields={"chat_id": chat_id},
            files={media_field: file_path},
        )
        return int(payload["result"]["message_id"])

    def _send_group(self, chat_id: str, files: list[Path]) -> list[int]:
        media = []
        attachments: dict[str, Path] = {}
        for idx, file in enumerate(files):
            attach_name = f"file{idx}"
            media.append(
                {
                    "type": "video" if self._is_video(file) else "photo",
                    "media": f"attach://{attach_name}",
                }
            )
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
        with urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _is_video(path: Path) -> bool:
        return path.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"}
