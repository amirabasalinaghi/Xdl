# X Retweet Media Relay Service Design (to Telegram)

## 1) Goal
Build a service that:
1. Watches a specific X (Twitter) account.
2. Detects new **retweets/reposts** made by that account.
3. Extracts every image/video from the reposted tweet.
4. Sends that media to a Telegram account through a Telegram bot.

> Note: X and Telegram API terms/rules can change. In production, use official APIs and ensure your usage complies with each platform’s Terms of Service and local law.

---

## 2) High-Level Architecture

```text
+----------------------+        +----------------------+        +----------------------+
| X Poller / Webhook   | -----> | Event Processor      | -----> | Media Fetcher        |
| (account timeline)   |        | (dedupe + queue)     |        | (download/transcode) |
+----------------------+        +----------------------+        +----------+-----------+
                                                                      |
                                                                      v
                                                               +------+------+
                                                               | Telegram    |
                                                               | Sender      |
                                                               +------+------+
                                                                      |
                                                                      v
                                                               +------+------+
                                                               | Telegram    |
                                                               | User/Chat   |
                                                               +-------------+

                   +-----------------------------------------------+
                   | PostgreSQL (state, idempotency, audit logs)   |
                   +-----------------------------------------------+
```

### Core components
- **X Poller**: Regularly queries the monitored account’s timeline for new reposts.
- **Event Processor**: Filters to reposts only, deduplicates, and enqueues work.
- **Media Fetcher**: Resolves and downloads media URLs; normalizes formats if needed.
- **Telegram Sender**: Uses Telegram Bot API (`sendPhoto`, `sendVideo`, `sendMediaGroup`) to deliver content.
- **State DB**: Persists last processed repost ID, media fingerprints, retry states, and delivery logs.

---

## 3) Data Model (minimal)

### `tracked_accounts`
- `id` (PK)
- `x_user_id` (unique)
- `x_handle`
- `telegram_chat_id`
- `is_active`
- `last_seen_tweet_id`
- `created_at`, `updated_at`

### `repost_events`
- `id` (PK)
- `x_repost_tweet_id` (unique)
- `x_original_tweet_id`
- `x_original_author`
- `posted_at`
- `raw_payload` (JSONB)
- `status` (`pending|processing|sent|failed`)
- `error_message`
- `created_at`, `updated_at`

### `media_items`
- `id` (PK)
- `repost_event_id` (FK)
- `media_type` (`photo|video|gif`)
- `source_url`
- `local_path` (or object storage key)
- `sha256` (for duplicate control)
- `width`, `height`, `duration_sec`
- `send_status`
- `created_at`, `updated_at`

### `delivery_logs`
- `id` (PK)
- `repost_event_id` (FK)
- `telegram_chat_id`
- `telegram_message_id`
- `sent_at`
- `api_response` (JSONB)

---

## 4) Workflow

1. **Scheduler tick** (e.g., every 20–60 seconds).
2. Poll X API for latest posts on target account, using `since_id = last_seen_tweet_id`.
3. Keep only repost/retweet entries.
4. For each new repost:
   - Insert into `repost_events` with unique constraint on `x_repost_tweet_id`.
   - If unique violation occurs, ignore (already processed).
5. Fetch media entities from the original tweet payload.
6. Download media files to temp/object storage.
7. Validate size/format for Telegram.
8. Send to Telegram:
   - Single image -> `sendPhoto`
   - Single video -> `sendVideo`
   - Multiple media -> `sendMediaGroup`
9. Store sent message IDs and mark `sent`.
10. Advance `last_seen_tweet_id`.

---

## 5) APIs and Integration Notes

## X side
- Preferred: official X API endpoints for user timeline/tweets expansions and media attachments.
- Request expansions/fields that include:
  - referenced tweets (to detect repost targets)
  - media keys + media URLs/variants
- Implement rate-limit aware polling with exponential backoff.

## Telegram side
- Create bot via BotFather.
- Bot token stored in secret manager.
- Ensure bot can message target chat:
  - For private chats: user starts bot first.
  - For channels/groups: bot must be added with required permissions.

---

## 6) Reliability & Idempotency

- **Exactly-once-ish delivery** achieved with:
  - Unique DB constraints (`x_repost_tweet_id`, media hash if needed).
  - Transactional state transitions (`pending -> processing -> sent/failed`).
- **Retry policy**:
  - Network/API failures: retry with exponential backoff and jitter.
  - Permanent failures (invalid media): mark failed and alert.
- **Dead-letter queue** for repeated failures.

---

## 7) Security

- Store secrets in env vars + secret manager (not in repo).
- Encrypt DB at rest and enforce TLS in transit.
- Validate downloaded URLs and content types.
- Set max file size and timeout to avoid abuse.
- Restrict service account permissions to minimum required scopes.

---

## 8) Observability

- Structured logs with correlation IDs (`repost_event_id`).
- Metrics:
  - Poll latency
  - Reposts detected/min
  - Delivery success rate
  - Retry count
  - End-to-end lag from repost to Telegram sent
- Alerts:
  - No polls succeeding for N minutes
  - Error rate above threshold
  - Queue backlog above threshold

---

## 9) Suggested Tech Stack

- **Language**: Python (FastAPI + Celery/RQ) or Node.js (NestJS/BullMQ).
- **Queue**: Redis or RabbitMQ.
- **DB**: PostgreSQL.
- **Object Storage**: S3-compatible bucket for temporary media.
- **Deployment**: Docker + one worker + one scheduler (or Kubernetes CronJob + worker).

---

## 10) Minimal MVP Plan

1. Single tracked X account, single Telegram chat.
2. Polling only (no streaming/webhooks).
3. Photos first, then add videos.
4. Basic dedupe by repost tweet ID.
5. Basic logs + health endpoint.

Then iterate:
- Multi-account support
- Better admin UI
- Better retries and dead-letter handling
- Advanced media normalization

---

## 11) Pseudocode (simplified)

```python
while True:
    account = db.get_tracked_account()
    posts = x_api.get_user_posts(account.x_user_id, since_id=account.last_seen_tweet_id)

    for post in sort_oldest_first(posts):
        if not is_repost(post):
            continue

        if db.exists_repost(post.id):
            continue

        event = db.create_repost_event(post)

        media_list = extract_media_from_original(post)
        files = []
        for media in media_list:
            file = download_media(media.url)
            files.append(file)
            db.create_media_item(event.id, media, file)

        tg_messages = telegram_send(account.telegram_chat_id, files)
        db.mark_sent(event.id, tg_messages)
        db.update_last_seen(account.id, max(account.last_seen_tweet_id, post.id))

    sleep(POLL_INTERVAL)
```

---

## 12) Legal/Policy Checklist (important)

Before production:
- Verify your usage complies with X developer policy and API terms.
- Verify redistribution of third-party media is allowed for your use case.
- Add user consent and content handling policy where applicable.
- Add a takedown process for reported content.
