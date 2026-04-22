from __future__ import annotations

import json
import logging
import os
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from xdl_relay.config import Settings
from xdl_relay.service import RelayService

logger = logging.getLogger(__name__)


HTML_PAGE = """<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>XDL Relay Dashboard</title>
  <style>
    :root { color-scheme: dark; }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
      background: radial-gradient(circle at top, #1e293b, #020617 42%);
      color: #e2e8f0;
      min-height: 100vh;
    }
    .container { max-width: 1200px; margin: 0 auto; padding: 24px; }
    .header {
      display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap;
      margin-bottom: 16px;
    }
    h1 { margin: 0; font-size: 1.6rem; }
    .muted { color: #94a3b8; }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 12px;
      margin-bottom: 16px;
    }
    .card {
      background: rgba(15, 23, 42, 0.78);
      border: 1px solid rgba(148, 163, 184, 0.18);
      border-radius: 14px;
      padding: 14px;
      backdrop-filter: blur(4px);
    }
    .value { font-size: 1.8rem; font-weight: 700; margin-top: 8px; }
    .toolbar { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; }
    input, select, button {
      border-radius: 10px;
      border: 1px solid #334155;
      background: #0f172a;
      color: #e2e8f0;
      padding: 9px 12px;
    }
    button {
      background: linear-gradient(135deg, #0ea5e9, #0284c7);
      border: none;
      cursor: pointer;
      font-weight: 600;
    }
    button:hover { filter: brightness(1.1); }
    table { width: 100%; border-collapse: collapse; }
    th, td { text-align: left; padding: 10px; border-bottom: 1px solid #1e293b; font-size: 0.92rem; }
    .status {
      padding: 3px 8px;
      border-radius: 999px;
      text-transform: uppercase;
      font-size: 0.7rem;
      font-weight: 700;
      letter-spacing: .04em;
    }
    .status-sent { background: rgba(34, 197, 94, 0.18); color: #86efac; }
    .status-failed { background: rgba(239, 68, 68, 0.18); color: #fca5a5; }
    .status-pending { background: rgba(234, 179, 8, 0.18); color: #fde68a; }
    .row { display: grid; grid-template-columns: 2fr 1fr; gap: 14px; }
    @media (max-width: 980px) { .row { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <div class=\"container\">
    <div class=\"header\">
      <div>
        <h1>XDL Relay Dashboard</h1>
        <div class=\"muted\" id=\"live\">Live data from relay.db</div>
      </div>
      <div class=\"toolbar\">
        <button id=\"process\">Process once now</button>
      </div>
    </div>

    <div class=\"grid\" id=\"stats\"></div>

    <section class=\"card\" style=\"margin-bottom:16px\">
      <h3>Configuration</h3>
      <div class=\"toolbar\">
        <input id=\"x_user_id\" placeholder=\"X_USER_ID\" />
        <input id=\"x_bearer_token\" placeholder=\"X_BEARER_TOKEN\" />
        <input id=\"telegram_bot_token\" placeholder=\"TELEGRAM_BOT_TOKEN\" />
        <input id=\"telegram_chat_id\" placeholder=\"TELEGRAM_CHAT_ID\" />
        <select id=\"media_download_mode\">
          <option value=\"both\">Download: Pictures + Videos</option>
          <option value=\"pic\">Download: Pictures only</option>
          <option value=\"video\">Download: Videos only</option>
        </select>
        <button id=\"save-settings\">Save settings</button>
      </div>
      <div class=\"muted\">Set IDs/keys here, then use Process once or enable polling.</div>
    </section>

    <div class=\"row\">
      <section class=\"card\">
        <h3>Repost events</h3>
        <div class=\"toolbar\">
          <select id=\"status\">
            <option value=\"\">All statuses</option>
            <option value=\"sent\">Sent</option>
            <option value=\"failed\">Failed</option>
            <option value=\"pending\">Pending</option>
          </select>
          <input id=\"query\" placeholder=\"Search tweet id\" />
          <button id=\"refresh\">Refresh</button>
        </div>
        <div style=\"overflow:auto\">
          <table>
            <thead>
              <tr><th>Repost ID</th><th>Original ID</th><th>Status</th><th>Updated</th><th>Error</th></tr>
            </thead>
            <tbody id=\"events\"></tbody>
          </table>
        </div>
      </section>

      <section class=\"card\">
        <h3>Delivery logs</h3>
        <div style=\"overflow:auto\">
          <table>
            <thead><tr><th>Repost</th><th>Message IDs</th><th>At</th></tr></thead>
            <tbody id=\"logs\"></tbody>
          </table>
        </div>
      </section>
    </div>
  </div>

  <script>
    async function getJson(url, options = {}) {
      const res = await fetch(url, options);
      if (!res.ok) throw new Error('Request failed: ' + res.status);
      return await res.json();
    }

    function card(label, value) {
      return `<div class=\"card\"><div class=\"muted\">${label}</div><div class=\"value\">${value}</div></div>`;
    }

    function statusBadge(status) {
      return `<span class=\"status status-${status}\">${status}</span>`;
    }

    async function loadOverview() {
      const o = await getJson('/api/overview');
      document.getElementById('stats').innerHTML = [
        card('Total events', o.total_events),
        card('Sent', o.sent_events),
        card('Failed', o.failed_events),
        card('Pending', o.pending_events),
        card('Last seen tweet', o.last_seen_tweet_id || '—'),
        card('Last updated', o.last_update || '—')
      ].join('');
      document.getElementById('live').textContent = `DB: ${o.db_path} • auto refresh every 10s`;
    }

    async function loadEvents() {
      const status = document.getElementById('status').value;
      const query = encodeURIComponent(document.getElementById('query').value.trim());
      const data = await getJson(`/api/events?limit=100&status=${encodeURIComponent(status)}&query=${query}`);
      document.getElementById('events').innerHTML = data.map(e => `
        <tr>
          <td>${e.repost_tweet_id}</td>
          <td>${e.original_tweet_id}</td>
          <td>${statusBadge(e.status)}</td>
          <td>${e.updated_at || e.created_at}</td>
          <td title=\"${(e.error_message || '').replace(/\"/g, '&quot;')}\">${(e.error_message || '').slice(0, 80)}</td>
        </tr>
      `).join('');
    }

    async function loadLogs() {
      const logs = await getJson('/api/logs?limit=50');
      document.getElementById('logs').innerHTML = logs.map(l => `
        <tr><td>${l.repost_tweet_id}</td><td>${l.telegram_message_ids || '—'}</td><td>${l.created_at}</td></tr>
      `).join('');
    }


    async function loadSettings() {
      const s = await getJson('/api/settings');
      document.getElementById('x_user_id').value = s.x_user_id || '';
      document.getElementById('x_bearer_token').value = s.x_bearer_token || '';
      document.getElementById('telegram_bot_token').value = s.telegram_bot_token || '';
      document.getElementById('telegram_chat_id').value = s.telegram_chat_id || '';
      document.getElementById('media_download_mode').value = s.media_download_mode || 'both';
    }

    async function saveSettings() {
      const payload = {
        x_user_id: document.getElementById('x_user_id').value.trim(),
        x_bearer_token: document.getElementById('x_bearer_token').value.trim(),
        telegram_bot_token: document.getElementById('telegram_bot_token').value.trim(),
        telegram_chat_id: document.getElementById('telegram_chat_id').value.trim(),
        media_download_mode: document.getElementById('media_download_mode').value
      };
      await getJson('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      alert('Settings saved.');
    }

    async function refreshAll() {
      await Promise.all([loadOverview(), loadEvents(), loadLogs()]);
    }

    document.getElementById('refresh').addEventListener('click', refreshAll);
    document.getElementById('save-settings').addEventListener('click', () => saveSettings().catch(err => alert(err.message)));
    document.getElementById('process').addEventListener('click', async () => {
      const btn = document.getElementById('process');
      btn.disabled = true;
      btn.textContent = 'Processing...';
      try {
        const result = await getJson('/api/process-once', { method: 'POST' });
        await refreshAll();
        alert(`Processed ${result.processed} repost event(s).`);
      } catch (err) {
        alert(err.message);
      } finally {
        btn.disabled = false;
        btn.textContent = 'Process once now';
      }
    });

    Promise.all([loadSettings(), refreshAll()]).catch(err => console.error(err));
    setInterval(() => refreshAll().catch(err => console.error(err)), 10000);
  </script>
</body>
</html>
"""


class DashboardServer:
    def __init__(self, relay_service: RelayService, host: str = "127.0.0.1", port: int = 8080, enable_poller: bool = True) -> None:
        self.relay_service = relay_service
        self.host = host
        self.port = port
        self.enable_poller = enable_poller
        self._stop_event = threading.Event()

    def _poll_loop(self) -> None:
        logger.info("Background poller started with interval=%ss", self.relay_service.settings.poll_interval_seconds)
        while not self._stop_event.is_set():
            try:
                processed = self.relay_service.process_once()
                if processed:
                    logger.info("WebUI background poll processed %s event(s)", processed)
            except Exception as exc:
                logger.exception("WebUI background poll failed: %s", exc)
            self._stop_event.wait(self.relay_service.settings.poll_interval_seconds)

    def run(self) -> None:
        handler = self._handler_factory()
        server = ThreadingHTTPServer((self.host, self.port), handler)

        poller = None
        if self.enable_poller:
            poller = threading.Thread(target=self._poll_loop, daemon=True)
            poller.start()

        logger.info("Dashboard available at http://%s:%s", self.host, self.port)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            logger.info("Dashboard interrupted, shutting down")
        finally:
            self._stop_event.set()
            server.server_close()
            if poller is not None:
                poller.join(timeout=2)

    def _handler_factory(self):
        relay_service = self.relay_service

        class Handler(BaseHTTPRequestHandler):
            def _json_response(self, payload: dict | list, status: HTTPStatus = HTTPStatus.OK) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _html_response(self, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
                data = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)

                if parsed.path == "/":
                    self._html_response(HTML_PAGE)
                    return

                if parsed.path == "/api/overview":
                    self._json_response(relay_service.db.get_overview())
                    return

                if parsed.path == "/api/events":
                    limit = _to_int(query.get("limit", ["100"])[0], 100)
                    status = query.get("status", [""])[0] or None
                    text_query = query.get("query", [""])[0] or None
                    self._json_response(relay_service.db.list_events(limit=limit, status=status, text_query=text_query))
                    return

                if parsed.path == "/api/logs":
                    limit = _to_int(query.get("limit", ["50"])[0], 50)
                    self._json_response(relay_service.db.list_delivery_logs(limit=limit))
                    return

                if parsed.path == "/api/settings":
                    self._json_response(_settings_payload(relay_service.settings))
                    return

                self._json_response({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)

            def do_POST(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path == "/api/process-once":
                    try:
                        processed = relay_service.process_once()
                        self._json_response({"processed": processed})
                    except Exception as exc:
                        logger.exception("Manual process_once failed: %s", exc)
                        self._json_response({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                    return

                if parsed.path == "/api/settings":
                    length = int(self.headers.get("Content-Length", "0"))
                    raw = self.rfile.read(length)
                    data = json.loads(raw.decode("utf-8") or "{}")
                    updated = Settings(
                        x_user_id=data.get("x_user_id") or relay_service.settings.x_user_id,
                        x_bearer_token=data.get("x_bearer_token") or relay_service.settings.x_bearer_token,
                        telegram_bot_token=data.get("telegram_bot_token") or relay_service.settings.telegram_bot_token,
                        telegram_chat_id=data.get("telegram_chat_id") or relay_service.settings.telegram_chat_id,
                        poll_interval_seconds=relay_service.settings.poll_interval_seconds,
                        db_path=relay_service.settings.db_path,
                        media_dir=relay_service.settings.media_dir,
                        http_timeout_seconds=relay_service.settings.http_timeout_seconds,
                        http_retries=relay_service.settings.http_retries,
                        http_backoff_seconds=relay_service.settings.http_backoff_seconds,
                        max_media_bytes=relay_service.settings.max_media_bytes,
                        x_max_pages=relay_service.settings.x_max_pages,
                        media_download_mode=_normalize_download_mode(
                            data.get("media_download_mode"), relay_service.settings.media_download_mode
                        ),
                        telegram_include_caption=relay_service.settings.telegram_include_caption,
                        telegram_failure_alerts=relay_service.settings.telegram_failure_alerts,
                    )
                    _write_env_file(updated)
                    relay_service.update_settings(updated)
                    self._json_response(_settings_payload(updated))
                    return

                self._json_response({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)

            def log_message(self, format: str, *args) -> None:  # noqa: A003
                logger.debug("dashboard http: %s", format % args)

        return Handler



def _env_file_path() -> str:
    return os.getenv("RELAY_ENV_FILE", "/etc/xdl-relay/xdl-relay.env")


def _write_env_file(settings: Settings) -> None:
    lines = [f"{k}={v}" for k, v in settings.to_env_dict().items()]
    with open(_env_file_path(), "w", encoding="utf-8") as fp:
        fp.write("\n".join(lines) + "\n")


def _settings_payload(settings: Settings) -> dict[str, str]:
    return settings.to_env_dict()


def _normalize_download_mode(raw_mode: str | None, default: str) -> str:
    mode = (raw_mode or default or "both").lower()
    if mode in {"pic", "video", "both"}:
        return mode
    return "both"

def _to_int(value: str, default: int) -> int:
    try:
        return max(1, int(value))
    except ValueError:
        return default
