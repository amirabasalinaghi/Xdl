# X -> Telegram Repost Media Relay

This service monitors a single X account for new reposts/retweets, downloads attached photos/videos from the original post, and forwards them to a Telegram chat via a bot.

## Features
- Poll X user timeline for new reposts.
- Dedupe by repost tweet ID using SQLite.
- Download every reposted media item to local disk.
- Send single or grouped media to Telegram.
- Add rich captions with original/repost links.
- Optional Telegram alert message when a repost relay fails.
- Persist delivery state and failures.

## Setup
1. Create an X developer app and get a bearer token.
2. Create a Telegram bot with BotFather and capture bot token.
3. Start a chat with your bot (or add bot to channel/group).
4. Copy `.env.example` values into your environment.

### Required command
```bash
pip install -e .
```

### Installer download links
- Linux service installer script: [`scripts/install_linux_service.sh`](scripts/install_linux_service.sh)
- Install scripts folder: [`scripts/`](scripts/)

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

Optional tuning environment variables:
- `HTTP_TIMEOUT_SECONDS` (default `30`)
- `HTTP_RETRIES` (default `3`)
- `HTTP_BACKOFF_SECONDS` (default `1.0`)
- `MAX_MEDIA_BYTES` (default `52428800`)
- `X_MAX_PAGES` (default `5`)
- `TELEGRAM_INCLUDE_CAPTION` (default `1`)
- `TELEGRAM_FAILURE_ALERTS` (default `1`)


## Install as a Linux service (guided installer)
### One-command install (clone + guided setup)
Replace `<REPO_URL>` with this repository URL, then run:

```bash
git clone <REPO_URL> /tmp/xdl-relay && bash /tmp/xdl-relay/scripts/install_linux_service.sh
```

This single command will:
- Download the code
- Launch the guided installer
- Install required system packages (when possible)
- Configure and start the `xdl-relay` systemd service

### If you already cloned the repo
Run the interactive installer script:

```bash
bash scripts/install_linux_service.sh
```

The installer will prompt you for all required values (`X_BEARER_TOKEN`, `X_USER_ID`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) and optional settings, then it will:
- Create a virtualenv under `/opt/xdl-relay/.venv`
- Install the package
- Write `/etc/xdl-relay/xdl-relay.env`
- Create and start a `systemd` service named `xdl-relay`

After install:

```bash
sudo systemctl status xdl-relay
sudo journalctl -u xdl-relay -f
```


## Web UI Dashboard
Run a modern, full-featured dashboard with live metrics, filters, delivery logs, and manual trigger controls:

```bash
python -m xdl_relay --webui --host 0.0.0.0 --port 8080
```

Options:
- `--no-poller`: opens the dashboard without background polling (manual trigger only).
- `--host` and `--port`: customize bind address.

Dashboard features:
- Real-time relay health cards (sent/failed/pending/last-seen tweet).
- Search + status filtering across repost events.
- Delivery log viewer for Telegram message IDs.
- One-click `Process once now` control for manual runs.

## Notes
- This is a polling MVP with one account and one Telegram destination.
- Ensure your usage complies with X and Telegram terms and local laws.
