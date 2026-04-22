from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from xdl_relay.config import Settings
from xdl_relay.service import RelayService

logger = logging.getLogger(__name__)

MIN_POLL_INTERVAL_SECONDS = 1
MAX_POLL_INTERVAL_SECONDS = 3600
MIN_HTTP_TIMEOUT_SECONDS = 1
MAX_HTTP_TIMEOUT_SECONDS = 300
MIN_HTTP_RETRIES = 1
MAX_HTTP_RETRIES = 10
MIN_HTTP_BACKOFF_SECONDS = 0.0
MAX_HTTP_BACKOFF_SECONDS = 60.0
MIN_MAX_MEDIA_BYTES = 1
MAX_MAX_MEDIA_BYTES = 50 * 1024 * 1024
MIN_X_MAX_PAGES = 1
MAX_X_MAX_PAGES = 100


class InMemoryLogHandler(logging.Handler):
    def __init__(self, capacity: int = 2000) -> None:
        super().__init__()
        self.capacity = capacity
        self._records: deque[dict[str, str]] = deque(maxlen=capacity)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created)),
                "level": record.levelname,
                "logger": record.name,
                "message": self.format(record),
            }
            with self._lock:
                self._records.append(entry)
        except Exception:
            self.handleError(record)

    def recent(self, limit: int = 1000, level: str | None = None) -> list[dict[str, str]]:
        normalized_level = (level or "").upper().strip()
        with self._lock:
            items = list(self._records)
        if normalized_level:
            items = [item for item in items if item["level"] == normalized_level]
        return list(reversed(items[-max(1, limit) :]))


_WEBUI_LOG_HANDLER: InMemoryLogHandler | None = None


def _get_or_create_webui_log_handler() -> InMemoryLogHandler:
    global _WEBUI_LOG_HANDLER
    if _WEBUI_LOG_HANDLER is None:
        handler = InMemoryLogHandler(capacity=5000)
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logging.getLogger().addHandler(handler)
        _WEBUI_LOG_HANDLER = handler
    return _WEBUI_LOG_HANDLER


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
    .fields {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 10px;
      margin-bottom: 12px;
    }
    .field { display: flex; flex-direction: column; gap: 6px; }
    .help { font-size: 0.76rem; color: #94a3b8; line-height: 1.35; }
    label { font-size: 0.82rem; color: #cbd5e1; }
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
    .saved-note { font-size: 0.76rem; color: #93c5fd; min-height: 1rem; }
    .log-level-info { color: #93c5fd; }
    .log-level-warning { color: #fde68a; }
    .log-level-error, .log-level-critical { color: #fca5a5; }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 0.84rem; }
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
        <button id=\"force-refresh\">Force refresh + retry unsent</button>
      </div>
    </div>

    <div class=\"grid\" id=\"stats\"></div>

    <section class=\"card\" style=\"margin-bottom:16px\">
      <h3>Relay Settings</h3>
      <div class=\"fields\">
        <div class=\"field\">
          <label for=\"x_user_id\">X Account User ID</label>
          <input id=\"x_user_id\" placeholder=\"e.g. 123456789\" />
          <div class=\"saved-note\" id=\"saved_x_user_id\"></div>
        </div>
        <div class=\"field\">
          <label for=\"x_bearer_token\">X API Bearer Token</label>
          <input id=\"x_bearer_token\" placeholder=\"Paste your bearer token\" />
          <div class=\"saved-note\" id=\"saved_x_bearer_token\"></div>
        </div>
        <div class=\"field\">
          <label for=\"telegram_bot_token\">Telegram Bot Token</label>
          <input id=\"telegram_bot_token\" placeholder=\"Paste bot token\" />
          <div class=\"saved-note\" id=\"saved_telegram_bot_token\"></div>
        </div>
        <div class=\"field\">
          <label for=\"telegram_chat_id\">Telegram Chat ID</label>
          <input id=\"telegram_chat_id\" placeholder=\"e.g. -1001234567890\" />
          <div class=\"saved-note\" id=\"saved_telegram_chat_id\"></div>
        </div>
        <div class=\"field\">
          <label for=\"media_download_mode\">Media Download Mode</label>
          <select id=\"media_download_mode\">
            <option value=\"both\">Download photos and videos</option>
            <option value=\"pic\">Download photos only</option>
            <option value=\"video\">Download videos only</option>
          </select>
          <div class=\"saved-note\" id=\"saved_media_download_mode\"></div>
        </div>
        <div class=\"field\">
          <label for=\"poll_interval_seconds\">Polling Interval (seconds, 1-3600)</label>
          <input id=\"poll_interval_seconds\" type=\"number\" min=\"1\" max=\"3600\" step=\"1\" />
          <div class=\"help\">How often the relay checks X for new reposts. Allowed range: 1 to 3600 seconds.</div>
          <div class=\"saved-note\" id=\"saved_poll_interval_seconds\"></div>
        </div>
        <div class=\"field\">
          <label for=\"http_timeout_seconds\">HTTP Timeout (seconds, 1-300)</label>
          <input id=\"http_timeout_seconds\" type=\"number\" min=\"1\" max=\"300\" step=\"1\" />
          <div class=\"help\">Maximum time to wait for each API or media download request before timing out. Allowed range: 1 to 300 seconds.</div>
          <div class=\"saved-note\" id=\"saved_http_timeout_seconds\"></div>
        </div>
        <div class=\"field\">
          <label for=\"http_retries\">HTTP Retries (1-10)</label>
          <input id=\"http_retries\" type=\"number\" min=\"1\" max=\"10\" step=\"1\" />
          <div class=\"help\">Number of retry attempts after a failed HTTP request. Allowed range: 1 to 10.</div>
          <div class=\"saved-note\" id=\"saved_http_retries\"></div>
        </div>
        <div class=\"field\">
          <label for=\"http_backoff_seconds\">Retry Backoff (seconds, 0-60)</label>
          <input id=\"http_backoff_seconds\" type=\"number\" min=\"0\" max=\"60\" step=\"0.1\" />
          <div class=\"help\">Delay multiplier between retries. Allowed range: 0 to 60 seconds.</div>
          <div class=\"saved-note\" id=\"saved_http_backoff_seconds\"></div>
        </div>
        <div class=\"field\">
          <label for=\"max_media_bytes\">Max Media Size (bytes, 1-52,428,800)</label>
          <input id=\"max_media_bytes\" type=\"number\" min=\"1\" max=\"52428800\" step=\"1\" />
          <div class=\"help\">Largest media file size allowed for download. Allowed range: 1 to 52,428,800 bytes (50 MB cloud Telegram Bot API max).</div>
          <div class=\"saved-note\" id=\"saved_max_media_bytes\"></div>
        </div>
        <div class=\"field\">
          <label for=\"x_max_pages\">X API Max Pages (1-100)</label>
          <input id=\"x_max_pages\" type=\"number\" min=\"1\" max=\"100\" step=\"1\" />
          <div class=\"help\">Maximum number of API pages fetched per sync cycle. Allowed range: 1 to 100 pages.</div>
          <div class=\"saved-note\" id=\"saved_x_max_pages\"></div>
        </div>
      </div>
      <div class=\"toolbar\">
        <button id=\"save-settings\">Save settings</button>
      </div>
      <div class=\"muted\">Update connection details, then trigger a manual process or let polling run.</div>
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
        <h3>Telegram Deliveries</h3>
        <div style=\"overflow:auto\">
          <table>
            <thead><tr><th>Repost</th><th>Message IDs</th><th>At</th></tr></thead>
            <tbody id=\"logs\"></tbody>
          </table>
        </div>
      </section>
    </div>

    <section class=\"card\" style=\"margin-top:16px;\">
      <h3>Comprehensive Application Log</h3>
      <div class=\"toolbar\">
        <select id=\"log-level\">
          <option value=\"\">All levels</option>
          <option value=\"INFO\">Info</option>
          <option value=\"WARNING\">Warning</option>
          <option value=\"ERROR\">Error</option>
          <option value=\"CRITICAL\">Critical</option>
        </select>
        <button id=\"refresh-logs\">Refresh logs</button>
      </div>
      <div style=\"overflow:auto; max-height: 360px;\">
        <table>
          <thead><tr><th>Time</th><th>Level</th><th>Logger</th><th>Message</th></tr></thead>
          <tbody id=\"system-logs\" class=\"mono\"></tbody>
        </table>
      </div>
    </section>
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
      const data = await getJson(`/api/events?limit=500&status=${encodeURIComponent(status)}&query=${query}`);
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
      const logs = await getJson('/api/logs?limit=200');
      document.getElementById('logs').innerHTML = logs.map(l => `
        <tr><td>${l.repost_tweet_id}</td><td>${l.telegram_message_ids || '—'}</td><td>${l.created_at}</td></tr>
      `).join('');
    }

    function maskSecret(value) {
      if (!value) return 'Not saved';
      if (value.length <= 8) return `Saved (${value.length} chars)`;
      return `Saved (${value.slice(0, 4)}…${value.slice(-4)})`;
    }

    function setSavedHint(id, text) {
      document.getElementById(`saved_${id}`).textContent = text;
    }

    async function loadSettings() {
      const s = await getJson('/api/settings');
      document.getElementById('x_user_id').value = s.x_user_id || '';
      document.getElementById('x_bearer_token').value = s.x_bearer_token || '';
      document.getElementById('telegram_bot_token').value = s.telegram_bot_token || '';
      document.getElementById('telegram_chat_id').value = s.telegram_chat_id || '';
      document.getElementById('media_download_mode').value = s.media_download_mode || 'both';
      document.getElementById('poll_interval_seconds').value = s.poll_interval_seconds || 15;
      document.getElementById('http_timeout_seconds').value = s.http_timeout_seconds || 60;
      document.getElementById('http_retries').value = s.http_retries || 5;
      document.getElementById('http_backoff_seconds').value = s.http_backoff_seconds || 2;
      document.getElementById('max_media_bytes').value = s.max_media_bytes || 52428800;
      document.getElementById('x_max_pages').value = s.x_max_pages || 64;

      setSavedHint('x_user_id', s.x_user_id ? `Saved: ${s.x_user_id}` : 'Not saved');
      setSavedHint('x_bearer_token', maskSecret(s.x_bearer_token));
      setSavedHint('telegram_bot_token', maskSecret(s.telegram_bot_token));
      setSavedHint('telegram_chat_id', s.telegram_chat_id ? `Saved: ${s.telegram_chat_id}` : 'Not saved');
      setSavedHint('media_download_mode', `Saved: ${s.media_download_mode || 'both'}`);
      setSavedHint('poll_interval_seconds', `Saved: ${s.poll_interval_seconds || 15}s`);
      setSavedHint('http_timeout_seconds', `Saved: ${s.http_timeout_seconds || 60}s`);
      setSavedHint('http_retries', `Saved: ${s.http_retries || 5}`);
      setSavedHint('http_backoff_seconds', `Saved: ${s.http_backoff_seconds || 2}s`);
      setSavedHint('max_media_bytes', `Saved: ${s.max_media_bytes || 52428800}`);
      setSavedHint('x_max_pages', `Saved: ${s.x_max_pages || 64}`);
    }

    async function loadSystemLogs() {
      const level = encodeURIComponent(document.getElementById('log-level').value);
      const logs = await getJson(`/api/system-logs?limit=1000&level=${level}`);
      document.getElementById('system-logs').innerHTML = logs.map(l => `
        <tr>
          <td>${l.time}</td>
          <td class="log-level-${l.level.toLowerCase()}">${l.level}</td>
          <td>${l.logger}</td>
          <td title="${(l.message || '').replace(/\"/g, '&quot;')}">${l.message || ''}</td>
        </tr>
      `).join('');
    }

    async function saveSettings() {
      const payload = {
        x_user_id: document.getElementById('x_user_id').value.trim(),
        x_bearer_token: document.getElementById('x_bearer_token').value.trim(),
        telegram_bot_token: document.getElementById('telegram_bot_token').value.trim(),
        telegram_chat_id: document.getElementById('telegram_chat_id').value.trim(),
        media_download_mode: document.getElementById('media_download_mode').value,
        poll_interval_seconds: document.getElementById('poll_interval_seconds').value,
        http_timeout_seconds: document.getElementById('http_timeout_seconds').value,
        http_retries: document.getElementById('http_retries').value,
        http_backoff_seconds: document.getElementById('http_backoff_seconds').value,
        max_media_bytes: document.getElementById('max_media_bytes').value,
        x_max_pages: document.getElementById('x_max_pages').value
      };
      await getJson('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      await loadSettings();
      alert('Settings saved.');
    }

    async function refreshAll() {
      await Promise.all([loadOverview(), loadEvents(), loadLogs(), loadSystemLogs()]);
    }

    document.getElementById('refresh').addEventListener('click', refreshAll);
    document.getElementById('refresh-logs').addEventListener('click', () => loadSystemLogs().catch(err => alert(err.message)));
    document.getElementById('log-level').addEventListener('change', () => loadSystemLogs().catch(err => console.error(err)));
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
    document.getElementById('force-refresh').addEventListener('click', async () => {
      const btn = document.getElementById('force-refresh');
      btn.disabled = true;
      btn.textContent = 'Refreshing...';
      try {
        const result = await getJson('/api/force-refresh-retry', { method: 'POST' });
        await refreshAll();
        alert(
          `Force refresh fetched ${result.fetched} repost(s). ` +
          `Retried ${result.retried} unsent and recovered ${result.retried_success}. ` +
          `Newly processed: ${result.new_processed}.`
        );
      } catch (err) {
        alert(err.message);
      } finally {
        btn.disabled = false;
        btn.textContent = 'Force refresh + retry unsent';
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
        self._log_handler = _get_or_create_webui_log_handler()

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
        relay_service_log_handler = self._log_handler

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
                    limit = _to_int(query.get("limit", ["500"])[0], 500)
                    status = query.get("status", [""])[0] or None
                    text_query = query.get("query", [""])[0] or None
                    self._json_response(relay_service.db.list_events(limit=limit, status=status, text_query=text_query))
                    return

                if parsed.path == "/api/logs":
                    limit = _to_int(query.get("limit", ["200"])[0], 200)
                    self._json_response(relay_service.db.list_delivery_logs(limit=limit))
                    return

                if parsed.path == "/api/settings":
                    self._json_response(_settings_payload(relay_service.settings))
                    return

                if parsed.path == "/api/system-logs":
                    limit = _to_int(query.get("limit", ["1000"])[0], 1000)
                    level = query.get("level", [""])[0] or None
                    self._json_response(relay_service_log_handler.recent(limit=limit, level=level))
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

                if parsed.path == "/api/force-refresh-retry":
                    try:
                        result = relay_service.force_refresh_and_retry_unsent()
                        self._json_response(result)
                    except Exception as exc:
                        logger.exception("Force refresh + retry failed: %s", exc)
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
                        poll_interval_seconds=_to_int_or_default(
                            data.get("poll_interval_seconds"),
                            relay_service.settings.poll_interval_seconds,
                            min_value=MIN_POLL_INTERVAL_SECONDS,
                            max_value=MAX_POLL_INTERVAL_SECONDS,
                        ),
                        db_path=relay_service.settings.db_path,
                        media_dir=relay_service.settings.media_dir,
                        http_timeout_seconds=_to_int_or_default(
                            data.get("http_timeout_seconds"),
                            relay_service.settings.http_timeout_seconds,
                            min_value=MIN_HTTP_TIMEOUT_SECONDS,
                            max_value=MAX_HTTP_TIMEOUT_SECONDS,
                        ),
                        http_retries=_to_int_or_default(
                            data.get("http_retries"),
                            relay_service.settings.http_retries,
                            min_value=MIN_HTTP_RETRIES,
                            max_value=MAX_HTTP_RETRIES,
                        ),
                        http_backoff_seconds=_to_float_or_default(
                            data.get("http_backoff_seconds"),
                            relay_service.settings.http_backoff_seconds,
                            min_value=MIN_HTTP_BACKOFF_SECONDS,
                            max_value=MAX_HTTP_BACKOFF_SECONDS,
                        ),
                        max_media_bytes=_to_int_or_default(
                            data.get("max_media_bytes"),
                            relay_service.settings.max_media_bytes,
                            min_value=MIN_MAX_MEDIA_BYTES,
                            max_value=MAX_MAX_MEDIA_BYTES,
                        ),
                        x_max_pages=_to_int_or_default(
                            data.get("x_max_pages"),
                            relay_service.settings.x_max_pages,
                            min_value=MIN_X_MAX_PAGES,
                            max_value=MAX_X_MAX_PAGES,
                        ),
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
    configured = os.getenv("RELAY_ENV_FILE")
    if configured:
        return configured
    default_path = "/etc/xdl-relay/xdl-relay.env"
    if os.access(os.path.dirname(default_path), os.W_OK):
        return default_path
    return ".env"


def _write_env_file(settings: Settings) -> None:
    path = _env_file_path()
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    lines = [f"{k}={v}" for k, v in settings.to_env_dict().items()]
    with open(path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(lines) + "\n")


def _settings_payload(settings: Settings) -> dict[str, str]:
    env_values = settings.to_env_dict()
    return {
        **env_values,
        "x_user_id": settings.x_user_id,
        "x_bearer_token": settings.x_bearer_token,
        "telegram_bot_token": settings.telegram_bot_token,
        "telegram_chat_id": settings.telegram_chat_id,
        "media_download_mode": settings.media_download_mode,
    }


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


def _to_int_or_default(
    value: object,
    default: int,
    min_value: int = 1,
    max_value: int | None = None,
) -> int:
    try:
        parsed = max(min_value, int(str(value)))
        if max_value is not None:
            return min(max_value, parsed)
        return parsed
    except (TypeError, ValueError):
        return default


def _to_float_or_default(
    value: object,
    default: float,
    min_value: float = 0.0,
    max_value: float | None = None,
) -> float:
    try:
        parsed = max(min_value, float(str(value)))
        if max_value is not None:
            return min(max_value, parsed)
        return parsed
    except (TypeError, ValueError):
        return default
