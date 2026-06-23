#!/usr/bin/env python3
"""Local management dashboard for the Str8Dex Discord bot."""

import json
import os
import re
import signal
import subprocess
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE_DIR = Path(__file__).parent
ENV_FILE = BASE_DIR / ".env"
CACHE_FILE = BASE_DIR / "cache.json"
LOG_FILE = BASE_DIR / "bot.log"
VENV_PYTHON = BASE_DIR / "venv" / "bin" / "python"
BOT_SCRIPT = BASE_DIR / "bot.py"
CACHE_TTL = 5 * 24 * 60 * 60

ENV_KEYS = ["POKEWALLET_KEY", "FORUM_CHANNEL_ID", "SEARCH_CHANNEL_ID"]


def read_env() -> dict:
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def write_env(updates: dict):
    env = read_env()
    env.update(updates)
    lines = []
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            stripped = line.strip()
            if "=" in stripped and not stripped.startswith("#"):
                k = stripped.split("=", 1)[0].strip()
                if k in env:
                    lines.append(f"{k}={env.pop(k)}")
                    continue
            lines.append(line)
    for k, v in env.items():
        lines.append(f"{k}={v}")
    ENV_FILE.write_text("\n".join(lines) + "\n")


def bot_pid() -> int | None:
    try:
        result = subprocess.run(
            ["pgrep", "-f", f"python.*{BOT_SCRIPT.name}"],
            capture_output=True, text=True
        )
        pids = [int(p) for p in result.stdout.split() if p.strip()]
        return pids[0] if pids else None
    except Exception:
        return None


def start_bot():
    if bot_pid():
        return "Bot is already running."
    log = open(LOG_FILE, "a")
    subprocess.Popen(
        [str(VENV_PYTHON), "-u", str(BOT_SCRIPT)],
        stdout=log, stderr=log,
        cwd=str(BASE_DIR),
        start_new_session=True,
    )
    time.sleep(1.5)
    return "Bot started." if bot_pid() else "Bot may have failed to start — check bot.log."


def stop_bot():
    pid = bot_pid()
    if not pid:
        return "Bot is not running."
    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(1)
        return "Bot stopped."
    except Exception as e:
        return f"Error stopping bot: {e}"


def cache_stats() -> dict:
    if not CACHE_FILE.exists():
        return {"total": 0, "active": 0, "expired": 0, "entries": []}
    try:
        data = json.loads(CACHE_FILE.read_text())
    except Exception:
        return {"total": 0, "active": 0, "expired": 0, "entries": []}
    now = time.time()
    entries = []
    for key, val in data.items():
        age = now - val.get("ts", 0)
        entries.append({
            "key": key,
            "cards": len(val.get("cards", [])),
            "age": age,
            "expired": age >= CACHE_TTL,
        })
    active = sum(1 for e in entries if not e["expired"])
    expired = len(entries) - active
    return {"total": len(entries), "active": active, "expired": expired, "entries": entries}


def clear_cache():
    if CACHE_FILE.exists():
        CACHE_FILE.write_text("{}")
    return "Cache cleared."


def fmt_age(seconds: float) -> str:
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    mins = int((seconds % 3600) // 60)
    if days:
        return f"{days}d {hours}h ago"
    if hours:
        return f"{hours}h {mins}m ago"
    return f"{mins}m ago"


def render_html(message: str = "") -> str:
    env = read_env()
    pid = bot_pid()
    status_text = f"Running (PID {pid})" if pid else "Stopped"
    status_color = "#2ecc71" if pid else "#e74c3c"
    status_dot = "🟢" if pid else "🔴"
    btn_action = "stop" if pid else "start"
    btn_label = "Stop Bot" if pid else "Start Bot"
    btn_color = "#e74c3c" if pid else "#2ecc71"

    cache = cache_stats()
    cache_rows = ""
    for e in sorted(cache["entries"], key=lambda x: x["age"]):
        query, _, lang = e["key"].partition("|")
        flag = "🇯🇵" if lang == "japanese" else "🇺🇸"
        style = "color:#e74c3c;" if e["expired"] else ""
        cache_rows += f"""
        <tr style="{style}">
          <td>{flag} {query}</td>
          <td>{e['cards']}</td>
          <td>{'⚠️ expired' if e['expired'] else fmt_age(e['age'])}</td>
        </tr>"""

    config_fields = ""
    for key in ENV_KEYS:
        field_type = "password" if "KEY" in key else "text"
        config_fields += f"""
        <div class="field">
          <label>{key}</label>
          <input type="{field_type}" name="{key}" value="" placeholder="Enter value..." autocomplete="off">
        </div>"""

    msg_html = f'<div class="msg">{message}</div>' if message else ""

    log_tail = ""
    if LOG_FILE.exists():
        lines = LOG_FILE.read_text().splitlines()
        log_tail = "\n".join(lines[-20:]) if lines else "(empty)"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Str8Dex Dashboard</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #1a1a2e; color: #e0e0e0; min-height: 100vh; }}
  header {{ background: #16213e; padding: 20px 32px; border-bottom: 2px solid #0f3460;
           display: flex; align-items: center; gap: 14px; }}
  header h1 {{ font-size: 1.6rem; color: #e94560; }}
  header span {{ font-size: 0.9rem; color: #888; }}
  .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px;
           padding: 28px 32px; max-width: 1100px; margin: 0 auto; }}
  .card {{ background: #16213e; border-radius: 10px; padding: 22px;
           border: 1px solid #0f3460; }}
  .card h2 {{ font-size: 1rem; text-transform: uppercase; letter-spacing: 1px;
              color: #e94560; margin-bottom: 16px; }}
  .status-row {{ display: flex; align-items: center; gap: 10px; margin-bottom: 16px; }}
  .status-badge {{ font-weight: 600; color: {status_color}; font-size: 1rem; }}
  button {{ padding: 9px 20px; border: none; border-radius: 6px; cursor: pointer;
            font-size: 0.9rem; font-weight: 600; transition: opacity .15s; }}
  button:hover {{ opacity: 0.85; }}
  .btn-bot {{ background: {btn_color}; color: #fff; }}
  .btn-save {{ background: #0f3460; color: #fff; }}
  .btn-clear {{ background: #e74c3c; color: #fff; }}
  .field {{ margin-bottom: 12px; }}
  .field label {{ display: block; font-size: 0.78rem; color: #888;
                  text-transform: uppercase; margin-bottom: 4px; }}
  .field input {{ width: 100%; padding: 8px 10px; background: #0f3460; border: 1px solid #1a4a80;
                  border-radius: 6px; color: #e0e0e0; font-size: 0.9rem; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.88rem; }}
  th {{ text-align: left; padding: 6px 8px; color: #888; font-weight: 500;
        border-bottom: 1px solid #0f3460; }}
  td {{ padding: 7px 8px; border-bottom: 1px solid #0f3460; }}
  .empty {{ color: #555; font-style: italic; font-size: 0.85rem; padding-top: 8px; }}
  .msg {{ background: #0f3460; border-left: 3px solid #e94560; padding: 10px 14px;
          border-radius: 4px; margin-bottom: 16px; font-size: 0.9rem; }}
  .stats {{ display: flex; gap: 18px; margin-bottom: 14px; font-size: 0.9rem; }}
  .stat {{ color: #888; }} .stat strong {{ color: #e0e0e0; }}
  pre {{ background: #0a0a1a; border-radius: 6px; padding: 12px; font-size: 0.78rem;
         line-height: 1.5; overflow-x: auto; max-height: 240px; overflow-y: auto;
         color: #aaa; border: 1px solid #0f3460; white-space: pre-wrap; }}
  .full {{ grid-column: 1 / -1; }}
</style>
</head>
<body>
<header>
  <h1>Str8Dex</h1>
  <span>Bot Dashboard · localhost</span>
</header>
<div class="grid">

  <!-- Bot Status -->
  <div class="card">
    <h2>Bot Status</h2>
    {msg_html}
    <div class="status-row">
      <span>{status_dot}</span>
      <span class="status-badge">{status_text}</span>
    </div>
    <form method="POST" action="/action">
      <input type="hidden" name="action" value="{btn_action}">
      <button class="btn-bot" type="submit">{btn_label}</button>
    </form>
  </div>

  <!-- Cache -->
  <div class="card">
    <h2>Cache</h2>
    <div class="stats">
      <div class="stat">Total <strong>{cache['total']}</strong></div>
      <div class="stat">Active <strong>{cache['active']}</strong></div>
      <div class="stat">Expired <strong>{cache['expired']}</strong></div>
    </div>
    <form method="POST" action="/action" style="margin-bottom:14px;">
      <input type="hidden" name="action" value="clear_cache">
      <button class="btn-clear" type="submit">Clear Cache</button>
    </form>
    {'<table><tr><th>Query</th><th>Cards</th><th>Cached</th></tr>' + cache_rows + '</table>' if cache['entries'] else '<p class="empty">Cache is empty.</p>'}
  </div>

  <!-- Config -->
  <div class="card full">
    <h2>Configuration (.env)</h2>
    <form method="POST" action="/action">
      <input type="hidden" name="action" value="save_config">
      {config_fields}
      <button class="btn-save" type="submit" style="margin-top:8px;">Save Config</button>
    </form>
  </div>

  <!-- Log -->
  <div class="card full">
    <h2>Bot Log (last 20 lines)</h2>
    <pre>{log_tail or '(no log file yet)'}</pre>
    <form method="POST" action="/action" style="margin-top:10px;">
      <input type="hidden" name="action" value="refresh">
      <button class="btn-save" type="submit">Refresh</button>
    </form>
  </div>

</div>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # silence request logs

    def send_html(self, body: str, status: int = 200):
        encoded = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        self.send_html(render_html())

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode()
        params = parse_qs(body)
        action = params.get("action", [""])[0]
        message = ""

        if action == "start":
            message = start_bot()
        elif action == "stop":
            message = stop_bot()
        elif action == "clear_cache":
            message = clear_cache()
        elif action == "save_config":
            updates = {k: params[k][0] for k in ENV_KEYS if k in params}
            write_env(updates)
            message = "Config saved."
        elif action == "refresh":
            message = ""

        self.send_html(render_html(message))


if __name__ == "__main__":
    host, port = "127.0.0.1", 5000
    server = HTTPServer((host, port), Handler)
    print(f"Dashboard running at http://{host}:{port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
