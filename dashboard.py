#!/usr/bin/env python3
"""Local management dashboard for the Str8Dex Discord bot."""

import hashlib
import hmac
import json
import os
import re
import secrets
import signal
import subprocess
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE_DIR = Path(__file__).parent
ENV_FILE = BASE_DIR / ".env"
CACHE_FILE = BASE_DIR / "cache.json"
LOG_FILE = BASE_DIR / "bot.log"
VENV_PYTHON = BASE_DIR / "venv" / "bin" / "python"
BOT_SCRIPT = BASE_DIR / "bot.py"
CREDENTIALS_FILE = BASE_DIR / "credentials.json"
CACHE_TTL = 5 * 24 * 60 * 60
SESSION_TTL = 24 * 60 * 60  # 24 hours

ENV_KEYS = ["POKEWALLET_KEY", "FORUM_CHANNEL_ID", "SEARCH_CHANNEL_ID"]

# In-memory session store: token -> expiry timestamp
_sessions: dict[str, float] = {}


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _hash_password(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 260_000).hex()

def _load_credentials() -> dict:
    if CREDENTIALS_FILE.exists():
        try:
            return json.loads(CREDENTIALS_FILE.read_text())
        except Exception:
            return {}
    return {}

def _save_credentials(username: str, password: str):
    salt = secrets.token_bytes(32)
    CREDENTIALS_FILE.write_text(json.dumps({
        "username": username,
        "salt": salt.hex(),
        "hash": _hash_password(password, salt),
    }))

def is_registered() -> bool:
    return bool(_load_credentials())

def verify_password(username: str, password: str) -> bool:
    creds = _load_credentials()
    if not creds or creds.get("username") != username:
        return False
    salt = bytes.fromhex(creds["salt"])
    expected = creds["hash"]
    actual = _hash_password(password, salt)
    return hmac.compare_digest(actual, expected)

def create_session() -> str:
    _purge_sessions()
    token = secrets.token_hex(32)
    _sessions[token] = time.time() + SESSION_TTL
    return token

def validate_session(token: str) -> bool:
    exp = _sessions.get(token)
    if exp and exp > time.time():
        return True
    _sessions.pop(token, None)
    return False

def _purge_sessions():
    now = time.time()
    expired = [t for t, exp in _sessions.items() if exp <= now]
    for t in expired:
        del _sessions[t]


# ── Env / bot helpers ─────────────────────────────────────────────────────────

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


# ── HTML pages ────────────────────────────────────────────────────────────────

_BASE_STYLE = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       background: #1a1a2e; color: #e0e0e0; min-height: 100vh;
       display: flex; align-items: center; justify-content: center; }
.card { background: #16213e; border-radius: 16px; padding: 48px 40px;
        border: 1px solid #0f3460; max-width: 400px; width: 90%; }
.logo { font-size: 2.4rem; text-align: center; margin-bottom: 12px; }
h1 { color: #e94560; font-size: 1.6rem; text-align: center; margin-bottom: 6px; }
.sub { color: #888; text-align: center; margin-bottom: 28px; font-size: 0.9rem; }
.field { margin-bottom: 16px; }
.field label { display: block; font-size: 0.78rem; color: #888;
               text-transform: uppercase; margin-bottom: 5px; }
.field input { width: 100%; padding: 10px 12px; background: #0f3460;
               border: 1px solid #1a4a80; border-radius: 8px;
               color: #e0e0e0; font-size: 0.95rem; }
.field input:focus { outline: 2px solid #e94560; }
button { width: 100%; padding: 11px; border: none; border-radius: 8px;
         cursor: pointer; font-size: 0.95rem; font-weight: 700;
         background: #e94560; color: #fff; margin-top: 4px; transition: opacity .15s; }
button:hover { opacity: 0.85; }
.err { background: #3a1020; border-left: 3px solid #e94560; padding: 10px 14px;
       border-radius: 4px; margin-bottom: 16px; font-size: 0.88rem; color: #f08080; }
.link { text-align: center; margin-top: 18px; font-size: 0.85rem; color: #888; }
.link a { color: #e94560; text-decoration: none; }
.link a:hover { text-decoration: underline; }
"""

def render_register_html(error: str = "") -> str:
    err_html = f'<div class="err">{error}</div>' if error else ""
    return f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><title>Str8Dex — Create Account</title>
<style>{_BASE_STYLE}</style></head><body>
<div class="card">
  <div class="logo">🃏</div>
  <h1>Str8Dex Dashboard</h1>
  <p class="sub">Create your admin account to get started.</p>
  {err_html}
  <form method="POST" action="/register">
    <div class="field"><label>Username</label>
      <input type="text" name="username" required autofocus autocomplete="off"></div>
    <div class="field"><label>Password</label>
      <input type="password" name="password" required></div>
    <div class="field"><label>Confirm Password</label>
      <input type="password" name="confirm" required></div>
    <button type="submit">Create Account</button>
  </form>
</div></body></html>"""


def render_login_html(error: str = "") -> str:
    err_html = f'<div class="err">{error}</div>' if error else ""
    register_link = '<div class="link"><a href="/register">Create an account</a></div>' if not is_registered() else ""
    return f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><title>Str8Dex — Login</title>
<style>{_BASE_STYLE}</style></head><body>
<div class="card">
  <div class="logo">🃏</div>
  <h1>Str8Dex Dashboard</h1>
  <p class="sub">Sign in to manage your bot.</p>
  {err_html}
  <form method="POST" action="/login">
    <div class="field"><label>Username</label>
      <input type="text" name="username" required autofocus autocomplete="username"></div>
    <div class="field"><label>Password</label>
      <input type="password" name="password" required autocomplete="current-password"></div>
    <button type="submit">Sign In</button>
  </form>
  {register_link}
</div></body></html>"""


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
           display: flex; align-items: center; justify-content: space-between; }}
  header h1 {{ font-size: 1.6rem; color: #e94560; }}
  header span {{ font-size: 0.9rem; color: #888; }}
  .logout {{ font-size: 0.85rem; color: #888; text-decoration: none;
             border: 1px solid #0f3460; padding: 6px 14px; border-radius: 6px; }}
  .logout:hover {{ color: #e94560; border-color: #e94560; }}
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
  <div style="display:flex;align-items:center;gap:14px;">
    <h1>Str8Dex</h1>
    <span>Bot Dashboard</span>
  </div>
  <a class="logout" href="/logout">Sign Out</a>
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


# ── HTTP handler ──────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _get_session_token(self) -> str | None:
        cookie_header = self.headers.get("Cookie", "")
        cookie = SimpleCookie(cookie_header)
        morsel = cookie.get("session")
        return morsel.value if morsel else None

    def _is_authed(self) -> bool:
        token = self._get_session_token()
        return bool(token and validate_session(token))

    def send_html(self, body: str, status: int = 200, extra_headers: list = None):
        encoded = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        if extra_headers:
            for name, value in extra_headers:
                self.send_header(name, value)
        self.end_headers()
        self.wfile.write(encoded)

    def redirect(self, location: str, extra_headers: list = None):
        self.send_response(303)
        self.send_header("Location", location)
        if extra_headers:
            for name, value in extra_headers:
                self.send_header(name, value)
        self.end_headers()

    def _parse_post(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode()
        return parse_qs(body)

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/register":
            if is_registered():
                self.redirect("/login")
            else:
                self.send_html(render_register_html())
            return

        if path == "/login":
            if self._is_authed():
                self.redirect("/")
            else:
                self.send_html(render_login_html())
            return

        if path == "/logout":
            token = self._get_session_token()
            if token:
                _sessions.pop(token, None)
            self.redirect("/login", [("Set-Cookie", "session=; Max-Age=0; HttpOnly; Path=/")])
            return

        if not self._is_authed():
            self.redirect("/login" if is_registered() else "/register")
            return

        self.send_html(render_html())

    def do_POST(self):
        path = urlparse(self.path).path
        params = self._parse_post()

        if path == "/register":
            if is_registered():
                self.redirect("/login")
                return
            username = params.get("username", [""])[0].strip()
            password = params.get("password", [""])[0]
            confirm  = params.get("confirm",  [""])[0]
            if not username or not password:
                self.send_html(render_register_html("Username and password are required."))
                return
            if password != confirm:
                self.send_html(render_register_html("Passwords do not match."))
                return
            if len(password) < 8:
                self.send_html(render_register_html("Password must be at least 8 characters."))
                return
            _save_credentials(username, password)
            token = create_session()
            cookie = f"session={token}; Max-Age={SESSION_TTL}; HttpOnly; Path=/"
            self.redirect("/", [("Set-Cookie", cookie)])
            return

        if path == "/login":
            username = params.get("username", [""])[0].strip()
            password = params.get("password", [""])[0]
            if verify_password(username, password):
                token = create_session()
                cookie = f"session={token}; Max-Age={SESSION_TTL}; HttpOnly; Path=/"
                self.redirect("/", [("Set-Cookie", cookie)])
            else:
                self.send_html(render_login_html("Invalid username or password."))
            return

        if not self._is_authed():
            self.redirect("/login" if is_registered() else "/register")
            return

        if path == "/action":
            action = params.get("action", [""])[0]
            message = ""
            if action == "start":
                message = start_bot()
            elif action == "stop":
                message = stop_bot()
            elif action == "clear_cache":
                message = clear_cache()
            elif action == "save_config":
                updates = {k: params[k][0] for k in ENV_KEYS if k in params and params[k][0]}
                if updates:
                    write_env(updates)
                message = "Config saved." if updates else "No changes — fields were empty."
            self.send_html(render_html(message))


if __name__ == "__main__":
    host, port = "0.0.0.0", 5000
    server = HTTPServer((host, port), Handler)
    print(f"Dashboard running at http://{host}:{port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
