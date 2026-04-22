# API limits used by this project

This table maps each API-related limit in the codebase to the current official maximum value in the provider docs.

| Area in code | Current script value | Official maximum | Notes |
|---|---:|---:|---|
| X timeline request `max_results` (`_timeline_params`) | `100` | `100` | For `GET /2/users/{id}/tweets`, docs specify `5 <= max_results <= 100`. For `GET /2/users/{id}/timelines/reverse_chronological`, docs specify `1 <= max_results <= 100`. |
| Telegram media caption length (`sendPhoto`/`sendVideo`/`sendMediaGroup`) | `caption[:1024]` | `1024` chars | Script already matches Telegram Bot API max caption length. |
| Telegram failure alert text (`sendMessage`) | `str(error)[:1000]` | `4096` chars | Telegram Bot API allows `1-4096` chars for `sendMessage.text`; script uses a safer internal cap. |
| Telegram media group size (`sendMediaGroup`) | no enforced cap in script | `10` items per group (and minimum `2`) | If more than 10 files are passed, Telegram API can reject the request. |
| Telegram file upload size (cloud Bot API) | local cap default `MAX_MEDIA_BYTES=209715200` (200 MiB) | generally up to `50 MB` via cloud Bot API methods like `sendVideo`/`sendDocument` | Current default local cap is above typical cloud Bot API upload max and should usually be lowered to avoid failed uploads. |
| Telegram file upload size (self-hosted Bot API server) | not used by this script (uses `api.telegram.org`) | up to `2000 MB` | Official docs note this higher limit only when using a local Bot API server. |

## Official references

- X API docs:
  - User posts timeline (`max_results` range): https://docs.x.com/x-api/users/get-posts
  - Reverse chronological timeline (`max_results` range): https://docs.x.com/x-api/users/get-timeline
- Telegram Bot API docs:
  - Main reference (`sendMessage`, `sendPhoto`, `sendVideo`, `sendMediaGroup`, local Bot API server section): https://core.telegram.org/bots/api
