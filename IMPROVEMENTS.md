# Xdl Improvement Backlog (50 Ideas)

This backlog is tailored to the current `xdl-relay` implementation and split into practical phases.

## Reliability & Correctness
1. Add retry with exponential backoff (and jitter) for all X/Telegram HTTP calls.
2. Handle Telegram `429` with dynamic sleep using `retry_after`.
3. Add per-request timeouts and global cycle timeout budget.
4. Save and resume in-progress repost processing after crashes.
5. Track retry count and last retry time in DB.
6. Add dead-letter status after N failed attempts.
7. Make `set_last_seen_tweet_id` advance only when processing succeeds.
8. Add transaction around `create_repost_event` + state changes for stronger idempotency.
9. Validate X payload schema defensively before parsing.
10. Add checksum validation after media download.

## Media Handling
11. Enforce max file size before download via `Content-Length`.
12. Stream downloads to disk instead of reading whole body in memory.
13. Add file extension/content-type normalization.
14. Support animated GIF handling (convert to MP4 when needed).
15. Add optional ffmpeg transcoding profile for Telegram compatibility.
16. Add per-media dedupe by SHA256, not just repost ID.
17. Add cleanup policy for old downloaded media files.
18. Add optional S3/object-storage backend for media artifacts.
19. Support caption templating (author, link, hashtags).
20. Preserve media ordering exactly as in original repost.

## X API Improvements
21. Add pagination support (follow `next_token`) for bursts > 20 tweets.
22. Pull additional tweet fields (`created_at`, `text`) for richer Telegram posts.
23. Add support for quote-repost handling as a configurable option.
24. Add configurable filters (ignore NSFW, keywords, specific authors).
25. Add "start from latest" bootstrap mode to avoid replaying history.
26. Add multi-account monitoring instead of a single `X_USER_ID`.
27. Add account-level allow/block lists.
28. Add adaptive polling interval based on rate limits and activity.
29. Add explicit rate-limit logging from X response headers.
30. Add webhook/streaming mode as alternative to polling.

## Telegram Delivery Features
31. Add Markdown/HTML caption formatting options.
32. Add topic/thread support for Telegram forums (`message_thread_id`).
33. Add chat destination routing rules per source account.
34. Add a dry-run mode that logs payload without sending.
35. Split large media groups to Telegram limits automatically.
36. Add fallback send strategy (group -> singles) on group failure.
37. Store Telegram API raw response for audit/debug.
38. Add post-send verification and resend reconciliation job.
39. Add optional pin message for important repost types.
40. Add inline keyboard actions (e.g., “Open original post”).

## Operations, DX, and Product
41. Replace raw `print`-style logging with structured JSON logs.
42. Add Prometheus metrics endpoint (`processed_total`, `failed_total`, lag).
43. Add health/readiness endpoints and self-check command.
44. Add OpenTelemetry tracing across poll/download/send phases.
45. Add admin CLI (`xdl-relay inspect/retry/requeue/state`).
46. Add full config validation with clear startup diagnostics.
47. Expand tests: integration tests with mocked X/Telegram responses.
48. Add CI pipeline (lint, type-check, tests, package build).
49. Add Dockerfile + docker-compose for one-command deployment.
50. Add lightweight web dashboard for status, failures, and replay.

## Suggested Execution Order
- **First 2 weeks:** 1, 2, 7, 11, 12, 21, 35, 42, 47, 48.
- **Next 2–4 weeks:** 5, 6, 15, 16, 26, 33, 36, 43, 45, 49.
- **Longer-term:** 18, 30, 44, 50 plus advanced routing/filtering.
