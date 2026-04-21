# X -> Telegram Repost Media Relay

This service monitors a single X account for new reposts/retweets, downloads attached photos/videos from the original post, and forwards them to a Telegram chat via a bot.

## Features
- Poll X user timeline for new reposts.
- Dedupe by repost tweet ID using SQLite.
- Download every reposted media item to local disk.
- Send single or grouped media to Telegram.
- Persist delivery state and failures.

## Setup
1. Create an X developer app and get a bearer token.
2. Create a Telegram bot with BotFather and capture bot token.
3. Start a chat with your bot (or add bot to channel/group).
4. Copy `.env.example` values into your environment.

## Run
```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
export X_BEARER_TOKEN=...
export X_USER_ID=...
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=...
python -m xdl_relay
```

## Notes
- This is a polling MVP with one account and one Telegram destination.
- Ensure your usage complies with X and Telegram terms and local laws.
