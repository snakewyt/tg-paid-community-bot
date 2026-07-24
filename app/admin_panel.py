"""Admin web panel: data dashboard + management backend.

Embedded in the existing FastAPI webhook service. Management actions:
plan create/edit/toggle, subscription search/extend/revoke, order browsing.

Auth: username + password login page → session cookie (HttpOnly, SameSite).
Default credentials: admin / 123456. First login forces password change;
the new password is persisted to data/admin_creds.json (PBKDF2 hashed).

CSRF: session cookie with SameSite=Strict, plus a hidden csrf_token field
in all POST forms that must match the session cookie value (defence in depth).
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import secrets
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select

from app.config import settings
from app.constants import PROVIDER_LABELS
from app.database import async_session_factory
from app.models.models import (
    BotChat,
    Order,
    OrderStatus,
    Plan,
    PromoAudience,
    PromoCampaign,
    PromoKind,
    Subscription,
    SubscriptionStatus,
    User,
)
from app.services.user_resolve import (
    find_user_ids_for_search,
    format_user_ref,
    resolve_telegram_user_id,
)
from app.utils import apply_expiry_delta, utcnow

logger = logging.getLogger(__name__)
admin_panel_router = APIRouter()

SESSION_COOKIE = "admin_session"
SESSION_MAX_AGE = 24 * 3600  # 24 hours

CREDS_FILE = Path("data/admin_creds.json")

# ----------------------------------------------------------------- credential store
# Credentials are read from data/admin_creds.json if it exists, otherwise
# from the .env-driven settings (default: admin / 123456).
# After the first password change the hashed password is persisted to disk.


def _hash_password(password: str, username: str) -> str:
    """PBKDF2-HMAC-SHA256 with the username as salt."""
    return hashlib.pbkdf2_hmac("sha256", password.encode(), username.encode(), 100_000).hex()


def _load_creds() -> tuple[str, str, bool]:
    """Return (username, password_hash, password_changed).

    Reads from CREDS_FILE if it exists, otherwise falls back to env defaults
    with password_changed=False.
    """
    try:
        data = json.loads(CREDS_FILE.read_text())
        return data["username"], data["password_hash"], data.get("changed", True)
    except Exception:
        username = settings.admin_panel_username
        pwd = settings.admin_panel_password
        return username, _hash_password(pwd, username), False


def _save_creds(username: str, password_hash: str) -> None:
    """Persist credentials to disk."""
    CREDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CREDS_FILE.write_text(
        json.dumps({"username": username, "password_hash": password_hash, "changed": True})
    )
    try:
        CREDS_FILE.chmod(0o600)
    except OSError:
        pass


def _verify_login(username: str, password: str) -> bool:
    """Check username/password against current stored credentials."""
    stored_user, stored_hash, _ = _load_creds()
    user_ok = secrets.compare_digest(username, stored_user)
    pwd_ok = secrets.compare_digest(_hash_password(password, stored_user), stored_hash)
    return user_ok and pwd_ok


def _password_changed() -> bool:
    _, _, changed = _load_creds()
    return changed


# ----------------------------------------------------------------- in-memory state

# In-memory session store: session_id → (username, expiry_timestamp)
_sessions: dict[str, tuple[str, float]] = {}

# Rate limiting: IP → [attempt_timestamps]
_login_attempts: dict[str, list[float]] = {}

SESSION_CLEANUP_INTERVAL = 600  # seconds between stale-entry sweeps


def _cleanup_stale_entries() -> int:
    """Remove expired sessions and stale rate-limit entries.
    Called periodically from the scheduler to prevent unbounded growth.
    Returns the number of entries removed.
    """
    now = time.time()
    removed = 0

    # Expired sessions
    stale_sids = [
        sid for sid, (_, exp) in _sessions.items() if now > exp
    ]
    for sid in stale_sids:
        _sessions.pop(sid, None)
        removed += 1

    # Rate-limit entries with no recent attempts
    stale_ips = [
        ip for ip, ts in _login_attempts.items()
        if not any(now - t < 60 for t in ts)
    ]
    for ip in stale_ips:
        _login_attempts.pop(ip, None)
        removed += 1

    if removed:
        logger.debug("Cleaned up %d stale in-memory entries", removed)
    return removed

def _check_login_rate(ip: str) -> bool:
    """Return False if IP has made >5 login attempts in the last 60s."""
    now = time.time()
    attempts = [t for t in _login_attempts.get(ip, []) if now - t < 60]
    _login_attempts[ip] = attempts
    if len(attempts) >= 5:
        return False
    _login_attempts[ip].append(now)
    return True


def _get_session(request: Request) -> str | None:
    """Return username if the session cookie is valid, else None.

    Extends session expiry on valid access.
    """
    session_id = request.cookies.get(SESSION_COOKIE)
    if not session_id:
        return None
    entry = _sessions.get(session_id)
    if not entry:
        return None
    username, expires_at = entry
    if time.time() > expires_at:
        _sessions.pop(session_id, None)
        return None
    # Extend session
    _sessions[session_id] = (username, time.time() + SESSION_MAX_AGE)
    return username


def _set_session(response: Response, username: str, secure: bool = False) -> str:
    """Create a new session, set the HttpOnly cookie, and return the session id."""
    session_id = secrets.token_urlsafe(32)
    _sessions[session_id] = (username, time.time() + SESSION_MAX_AGE)
    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        httponly=True,
        max_age=SESSION_MAX_AGE,
        samesite="strict",
        secure=secure,
    )
    return session_id


def _clear_session(response: Response, session_id: str | None = None) -> None:
    if session_id:
        _sessions.pop(session_id, None)
    response.delete_cookie(SESSION_COOKIE, samesite="strict")


def _csrf_check(request: Request, form_csrf: str) -> bool:
    """Verify that the hidden csrf_token matches the session cookie."""
    session_id = request.cookies.get(SESSION_COOKIE)
    if not session_id or not form_csrf:
        return False
    return secrets.compare_digest(session_id, form_csrf)


def _redirect(msg: str, err: bool = False, *, to: str = "/admin") -> RedirectResponse:
    flag = "err" if err else "ok"
    return RedirectResponse(url=f"{to}?msg={quote(msg)}&t={flag}", status_code=303)


# ---------------------------------------------------------------------- styles
# Shared CSS block. Kept separate so each template can include it.
# NOTE: braces are doubled ({{}}) so Python .format() leaves them as single { }.

_STYLES = """<style>
  :root, [data-theme="dark"] {{
    --bg: #0f1419; --card: #1a2129; --border: #2a3441;
    --input-bg: #212a35; --th-bg: #212a35;
    --text: #e6edf3; --muted: #8b98a5; --accent: #4f9cf9;
    --accent-hover: #3d8de8;
    --green: #3fb950; --red: #f85149; --orange: #d29922;
    --sidebar-bg: #1e1b4b;
    --sidebar-border: rgba(255,255,255,.08);
    --sidebar-divider: rgba(255,255,255,.1);
    --sidebar-title: #fff;
    --sidebar-sub: rgba(255,255,255,.45);
    --sidebar-user: rgba(255,255,255,.65);
    --sidebar-nav: rgba(255,255,255,.7);
    --sidebar-nav-hover-bg: rgba(255,255,255,.08);
    --sidebar-nav-active-bg: rgba(255,255,255,.13);
    --sidebar-nav-active: #fff;
  }}
  [data-theme="light"] {{
    --bg: #f0f4f8; --card: #ffffff; --border: #d0d7de;
    --input-bg: #ffffff; --th-bg: #f6f8fa;
    --text: #1f2328; --muted: #656d76; --accent: #0969da;
    --accent-hover: #0550ae;
    --green: #1a7f37; --red: #cf222e; --orange: #9a6700;
    --sidebar-bg: #ffffff;
    --sidebar-border: #d0d7de;
    --sidebar-divider: #d0d7de;
    --sidebar-title: #1f2328;
    --sidebar-sub: #656d76;
    --sidebar-user: #656d76;
    --sidebar-nav: #656d76;
    --sidebar-nav-hover-bg: #f6f8fa;
    --sidebar-nav-active-bg: #ddf4ff;
    --sidebar-nav-active: #0969da;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", sans-serif;
    padding: 24px; max-width: 1400px; margin: 0 auto;
    min-height: 100vh;
  }}
  h1 {{ font-size: 22px; margin-bottom: 4px; }}
  .sub {{ color: var(--muted); font-size: 13px; margin-bottom: 20px; }}
  .flash {{ background: rgba(63,185,80,.12); border: 1px solid var(--green);
    color: var(--green); border-radius: 8px; padding: 10px 14px; margin-bottom: 18px; font-size: 13px; }}
  .flash.err {{ background: rgba(248,81,73,.12); border-color: var(--red); color: var(--red); }}
  .flash.warn {{ background: rgba(210,153,34,.12); border-color: var(--orange); color: var(--orange); }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 14px; margin-bottom: 26px; }}
  .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 16px; }}
  .card .label {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .5px; }}
  .card .value {{ font-size: 24px; font-weight: 600; margin-top: 6px; }}
  h2 {{ font-size: 15px; margin: 26px 0 10px; color: var(--accent); }}
  table {{ width: 100%; border-collapse: collapse; background: var(--card); border-radius: 10px; overflow: hidden; font-size: 13px; }}
  th {{ text-align: left; padding: 9px 12px; background: var(--th-bg); color: var(--muted); font-weight: 500; white-space: nowrap; }}
  td {{ padding: 8px 12px; border-top: 1px solid var(--border); vertical-align: middle; }}
  .tag {{ display: inline-block; padding: 1px 8px; border-radius: 20px; font-size: 11px; }}
  .tag.active, .tag.fulfilled {{ background: rgba(63,185,80,.15); color: var(--green); }}
  .tag.inactive, .tag.expired, .tag.kicked, .tag.cancelled {{ background: rgba(139,152,165,.15); color: var(--muted); }}
  .tag.pending, .tag.paid {{ background: rgba(79,156,249,.15); color: var(--accent); }}
  .empty {{ color: var(--muted); padding: 16px; text-align: center; }}
  form.inline {{ display: inline-flex; gap: 4px; align-items: center; margin: 0; }}
  input, select, button {{
    background: var(--input-bg); color: var(--text); border: 1px solid var(--border);
    border-radius: 6px; padding: 5px 8px; font-size: 12px;
  }}
  input:focus, select:focus {{ outline: 1px solid var(--accent); }}
  button {{ cursor: pointer; color: var(--accent); border-color: var(--accent); background: transparent; white-space: nowrap; }}
  button:hover {{ background: rgba(79,156,249,.12); }}
  button.danger {{ color: var(--red); border-color: var(--red); }}
  button.danger:hover {{ background: rgba(248,81,73,.12); }}
  .formrow {{ background: var(--card); border: 1px solid var(--border); border-radius: 10px;
    padding: 14px; margin-bottom: 10px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }}
  .formrow span {{ color: var(--muted); font-size: 12px; }}
  .w60 {{ width: 60px; }} .w80 {{ width: 80px; }} .w90 {{ width: 90px; }} .w130 {{ width: 130px; }} .w160 {{ width: 160px; }}

  /* Login / Change-password page */
  .login-wrap {{
    display: flex; align-items: center; justify-content: center; min-height: 100vh;
  }}
  .login-box {{
    background: var(--card); border: 1px solid var(--border); border-radius: 16px;
    padding: 40px 36px; width: 100%; max-width: 380px;
  }}
  .login-box h1 {{ text-align: center; margin-bottom: 8px; }}
  .login-box .sub {{ text-align: center; margin-bottom: 24px; }}
  .login-box input {{
    display: block; width: 100%; padding: 10px; margin-bottom: 12px;
    font-size: 14px; border-radius: 8px;
  }}
  .login-box button {{
    display: block; width: 100%; padding: 10px; font-size: 14px;
    border-radius: 8px; margin-top: 4px;
  }}
  .login-box .hint {{
    color: var(--muted); font-size: 11px; margin-bottom: 12px; text-align: center;
  }}

  /* Header bar */
  .header {{
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 20px; flex-wrap: wrap; gap: 8px;
  }}
  .header .right {{ color: var(--muted); font-size: 13px; }}
  .header .right a {{ color: var(--accent); text-decoration: none; margin-left: 12px; }}
  .header .right a.logout {{ color: var(--red); }}
  .header .right a:hover {{ text-decoration: underline; }}

  /* ── Sidebar layout ── */
  body.layout-body {{ padding: 0; max-width: none; }}
  .layout {{ display: flex; min-height: 100vh; }}
  .sidebar {{
    width: 220px; min-width: 220px; background: var(--sidebar-bg);
    display: flex; flex-direction: column;
    border-right: 1px solid var(--sidebar-border);
  }}
  .sidebar-brand {{
    padding: 20px 16px 16px;
    border-bottom: 1px solid var(--sidebar-divider);
  }}
  .brand-title {{
    font-size: 15px; font-weight: 700; color: var(--sidebar-title); line-height: 1.3;
  }}
  .brand-sub {{ font-size: 11px; color: var(--sidebar-sub); margin-top: 2px; }}
  .brand-user {{ font-size: 12px; color: var(--sidebar-user); margin-top: 8px; }}
  .sidebar-nav {{
    flex: 1; padding: 8px 0; display: flex; flex-direction: column;
  }}
  .nav-item {{
    display: flex; align-items: center; gap: 10px;
    padding: 11px 18px; color: var(--sidebar-nav);
    text-decoration: none; font-size: 13px;
    transition: background .15s; border-left: 3px solid transparent;
  }}
  .nav-item:hover {{ background: var(--sidebar-nav-hover-bg); color: var(--sidebar-nav-active); }}
  .nav-item.active {{
    background: var(--sidebar-nav-active-bg); color: var(--sidebar-nav-active); font-weight: 500;
    border-left-color: var(--accent);
  }}
  .sidebar-footer {{
    border-top: 1px solid var(--sidebar-divider);
    padding: 8px 0;
  }}
  .theme-switcher {{
    display: flex; align-items: center; gap: 8px;
    padding: 10px 18px 8px;
  }}
  .theme-label {{ font-size: 12px; color: var(--sidebar-sub); flex: 1; }}
  .theme-btn {{
    background: transparent; border: 1px solid var(--sidebar-divider);
    border-radius: 8px; padding: 5px 10px; font-size: 15px;
    cursor: pointer; line-height: 1; opacity: .55;
    color: inherit;
  }}
  .theme-btn:hover {{ opacity: .85; background: var(--sidebar-nav-hover-bg); }}
  .theme-btn.active {{ opacity: 1; border-color: var(--accent); background: var(--sidebar-nav-active-bg); }}
  .nav-item.nav-logout {{ color: var(--red); }}
  .nav-item.nav-logout:hover {{ background: rgba(248,81,73,.1); }}
  .login-theme {{
    position: fixed; top: 16px; right: 16px;
    display: flex; gap: 6px; z-index: 10;
  }}
  .nav-icon {{ width: 18px; text-align: center; }}
  .main-content {{ flex: 1; padding: 28px 32px; overflow: auto; min-width: 0; }}
  .page-hdr {{ margin-bottom: 22px; }}
  .page-hdr h1 {{ font-size: 20px; margin-bottom: 4px; }}

  /* ── Bot-config form cards ── */
  .sc {{
    background: var(--card); border: 1px solid var(--border);
    border-radius: 12px; padding: 24px; margin-bottom: 20px;
  }}
  .sc > h2 {{
    font-size: 14px; font-weight: 600; color: var(--accent);
    margin: 0 0 18px; display: flex; align-items: center; gap: 8px;
  }}
  .frow {{ margin-bottom: 16px; }}
  .frow label {{
    display: block; font-size: 13px; font-weight: 500;
    margin-bottom: 5px; color: var(--text);
  }}
  .frow .req {{ color: var(--red); }}
  .frow input[type=text], .frow input[type=number],
  .frow input[type=password], .frow textarea {{
    width: 100%; max-width: 500px; padding: 9px 12px; font-size: 13px;
    border-radius: 8px; background: var(--input-bg); color: var(--text);
    border: 1px solid var(--border); display: block;
  }}
  .frow input:focus, .frow textarea:focus {{ outline: 1px solid var(--accent); }}
  .frow textarea {{ resize: vertical; min-height: 88px; font-family: inherit; line-height: 1.5; }}
  .fhint {{ font-size: 11px; color: var(--muted); margin-top: 4px; }}
  .btn-save {{
    background: var(--accent); color: #fff; border: none;
    padding: 10px 36px; font-size: 14px; border-radius: 8px;
    cursor: pointer; font-weight: 500;
  }}
  .btn-save:hover {{ background: var(--accent-hover); }}
  .backend-section.hidden {{ display: none; }}
  .backend-hint {{ color: var(--muted); font-size: 12px; margin: 4px 0 18px; line-height: 1.5; }}
</style>"""

_THEME_BOOTSTRAP = """<script>
(function () {{
  var k = 'admin_theme', s = localStorage.getItem(k);
  var t = s || (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
  document.documentElement.setAttribute('data-theme', t);
}})();
</script>"""

_THEME_SCRIPT = """<script>
(function () {{
  var k = 'admin_theme';
  function apply(t) {{
    document.documentElement.setAttribute('data-theme', t);
    localStorage.setItem(k, t);
    document.querySelectorAll('[data-theme-set]').forEach(function (b) {{
      b.classList.toggle('active', b.getAttribute('data-theme-set') === t);
    }});
  }}
  function current() {{
    return document.documentElement.getAttribute('data-theme') || 'dark';
  }}
  document.addEventListener('click', function (e) {{
    var btn = e.target.closest('[data-theme-set]');
    if (btn) apply(btn.getAttribute('data-theme-set'));
  }});
  apply(current());
}})();
</script>"""

_THEME_TOGGLE = (
    '<button type="button" class="theme-btn" data-theme-set="dark" title="暗色" aria-label="暗色">🌙</button>'
    '<button type="button" class="theme-btn" data-theme-set="light" title="亮色" aria-label="亮色">☀️</button>'
)

_HEAD_ASSETS = _THEME_BOOTSTRAP + _STYLES

# ----------------------------------------------------------------- login page

LOGIN_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Admin Login</title>
""" + _HEAD_ASSETS + """
</head>
<body>
<div class="login-theme">{theme_toggle}</div>
<div class="login-wrap">
  <div class="login-box">
    <h1>管理后台</h1>
    <p class="sub">请输入用户名和密码</p>
    {flash}
    <form method="post" action="/admin/login">
      <input type="text" name="username" placeholder="用户名" required autocomplete="username">
      <input type="password" name="password" placeholder="密码" required autocomplete="current-password">
      <button type="submit">登录</button>
    </form>
  </div>
</div>
""" + _THEME_SCRIPT + """
</body>
</html>"""

# ---------------------------------------------------------- change password page

CHANGE_PASSWORD_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>修改密码 — 管理后台</title>
""" + _HEAD_ASSETS + """
</head>
<body class="layout-body">
<div class="layout">
{sidebar}
<main class="main-content">
<div class="page-hdr"><h1>修改管理员密码</h1></div>
{flash}
<div class="sc" style="max-width:480px;">
  <form method="post" action="/admin/change-password">
    <input type="hidden" name="csrf_token" value="{csrf_token}">
    <div class="frow">
      <label>当前密码</label>
      <input type="password" name="current_password" placeholder="当前密码" required autocomplete="current-password">
    </div>
    <div class="frow">
      <label>新密码 <span class="req">*</span></label>
      <input type="password" name="new_password" placeholder="新密码（至少8位）" required minlength="8" autocomplete="new-password">
    </div>
    <div class="frow">
      <label>确认新密码 <span class="req">*</span></label>
      <input type="password" name="confirm_password" placeholder="确认新密码" required minlength="8" autocomplete="new-password">
    </div>
    <div class="fhint" style="margin-bottom:16px;">密码修改后将写入服务器配置文件，不会丢失。</div>
    <button class="btn-save" type="submit">修改密码</button>
  </form>
</div>
</main>
</div>
""" + _THEME_SCRIPT + """
</body>
</html>"""

# ---------------------------------------------------------------- dashboard

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>管理后台 — 首页</title>
""" + _HEAD_ASSETS + """
</head>
<body class="layout-body">
<div class="layout">
{sidebar}
<main class="main-content">
<div class="page-hdr">
  <h1>管理后台</h1>
  <div class="sub">{generated_at} UTC · 数据实时</div>
</div>
{flash}

<div class="cards">
  <div class="card"><div class="label">总用户</div><div class="value">{total_users}</div></div>
  <div class="card"><div class="label">活跃会员</div><div class="value">{active_subs}</div></div>
  <div class="card"><div class="label">已完成订单</div><div class="value">{fulfilled_orders}</div></div>
  <div class="card"><div class="label">近 7 天新订单</div><div class="value">{orders_7d}</div></div>
  {revenue_cards}
</div>

<h2>收入按支付渠道</h2>
{provider_table}

<h2>最近订单</h2>
{orders_table}

</main>
</div>
""" + _THEME_SCRIPT + """
</body>
</html>"""


PLANS_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>套餐管理 — 管理后台</title>
""" + _HEAD_ASSETS + """
</head>
<body class="layout-body">
<div class="layout">
{sidebar}
<main class="main-content">
<div class="page-hdr">
  <div>
    <div class="page-title">套餐管理</div>
    <div class="page-sub">群组 / 频道与套餐定价</div>
  </div>
</div>
{flash}

<h2>机器人已加入的群组 / 频道</h2>
<p style="color:var(--muted);font-size:13px;margin:0 0 10px">把机器人加入群组并设为管理员后，这里会自动出现；创建套餐时可从下方下拉选择 chat_id。</p>
{bot_chats_table}

<h2>套餐列表</h2>
<form method="post" action="/admin/plans/create" style="margin:0">
<input type="hidden" name="csrf_token" value="{csrf_token}">
<div class="formrow" style="flex-direction:column;align-items:stretch;gap:10px">
  <div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center">
    <span>新增套餐:</span>
    <input class="w130" name="name" placeholder="名称" required>
    <input class="w60" name="duration_days" type="number" min="1" placeholder="天数" required>
    <select class="w160" id="chat_id_picker"
      onchange="if(this.value){{document.getElementById('chat_id_input').value=this.value;}}"
      title="选择机器人已加入的群/频道，会自动填入右侧输入框">
      <option value="">▼ 选择已加入的群/频道</option>
      {chat_options}
    </select>
    <input class="w160" id="chat_id_input" name="chat_id" placeholder="或手动输入 chat_id"
      required title="可从左侧下拉选择，也可手动输入；创建后不可修改">
  </div>
  <div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center">
    <span style="color:var(--muted);font-size:12px">价格 (填0=不启用该渠道):</span>
    <span style="font-size:12px;color:var(--muted)">Stars:</span><input class="w60" name="price_stars" type="number" placeholder="XTR" value="0">
    <span style="font-size:12px;color:var(--muted)">USDT:</span><input class="w60" name="price_crypto" type="number" step="0.01" placeholder="USDT" value="0">
    <span style="font-size:12px;color:var(--muted)">Stripe美分:</span><input class="w60" name="price_stripe" type="number" placeholder="美分" value="0">
    <span style="font-size:12px;color:var(--muted)">支付宝CNY:</span><input class="w80" name="price_alipay" type="number" step="0.01" placeholder="¥" value="0">
    <span style="font-size:12px;color:var(--muted)">微信CNY:</span><input class="w80" name="price_wechat" type="number" step="0.01" placeholder="¥" value="0">
    <button type="submit">创建套餐</button>
  </div>
</div>
</form>
{plans_table}

</main>
</div>
""" + _THEME_SCRIPT + """
</body>
</html>"""


def _esc(v) -> str:
    return html.escape(str(v if v is not None else ""))


_NAV_ITEMS = [
    ("dashboard", "/admin", "📊", "首页"),
    ("plans", "/admin/plans", "📦", "套餐管理"),
    ("members", "/admin/members", "👥", "会员管理"),
    ("bot-config", "/admin/bot-config", "⚙️", "机器人配置"),
    ("settings", "/admin/settings", "💳", "支付设置"),
    ("change-password", "/admin/change-password", "🔒", "修改密码"),
]


def _nav_html(active: str, username: str) -> str:
    """Build the sidebar navigation HTML block."""
    items = ""
    for k, url, icon, label in _NAV_ITEMS:
        cls = " active" if k == active else ""
        items += (
            f'<a class="nav-item{cls}" href="{url}">'
            f'<span class="nav-icon">{icon}</span>{_esc(label)}</a>'
        )
    items += (
        '<div class="sidebar-footer">'
        '<div class="theme-switcher">'
        '<span class="theme-label">主题</span>'
        f'{_THEME_TOGGLE}'
        '</div>'
        '<a class="nav-item nav-logout" href="/admin/logout">'
        '<span class="nav-icon">🚪</span>退出登录</a>'
        '</div>'
    )
    return (
        '<aside class="sidebar">'
        '<div class="sidebar-brand">'
        '<div class="brand-title">VIP 付费社群</div>'
        '<div class="brand-sub">管理系统</div>'
        f'<div class="brand-user">👤 {_esc(username)}</div>'
        '</div>'
        f'<nav class="sidebar-nav">{items}</nav>'
        '</aside>'
    )


# ---------------------------------------------------------------- login / logout


@admin_panel_router.get("/admin/login", response_class=HTMLResponse)
async def login_page(request: Request, msg: str = ""):
    if _get_session(request):
        return RedirectResponse(url="/admin", status_code=303)

    flash = ""
    if msg:
        flash = f'<div class="flash err">{_esc(msg)}</div>'

    return HTMLResponse(content=LOGIN_PAGE.format(flash=flash, theme_toggle=_THEME_TOGGLE))


@admin_panel_router.post("/admin/login", response_class=HTMLResponse)
async def login_submit(request: Request):
    ip = request.client.host if request.client else "0.0.0.0"
    if not _check_login_rate(ip):
        return HTMLResponse(
            content=LOGIN_PAGE.format(
                flash='<div class="flash err">登录尝试过于频繁，请 60 秒后重试</div>',
                theme_toggle=_THEME_TOGGLE,
            ),
            status_code=429,
        )

    form = await request.form()
    username = form.get("username", "")
    password = form.get("password", "")

    if not isinstance(username, str) or not isinstance(password, str):
        return HTMLResponse(
            content=LOGIN_PAGE.format(
                flash='<div class="flash err">用户名或密码错误</div>',
                theme_toggle=_THEME_TOGGLE,
            ),
            status_code=401,
        )

    if not _verify_login(username, password):
        return HTMLResponse(
            content=LOGIN_PAGE.format(
                flash='<div class="flash err">用户名或密码错误</div>',
                theme_toggle=_THEME_TOGGLE,
            ),
            status_code=401,
        )

    secure = request.headers.get("X-Forwarded-Proto", "http") == "https"
    if not _password_changed():
        response = RedirectResponse(
            url="/admin/change-password?msg=首次登录，请修改默认密码",
            status_code=303,
        )
    else:
        response = RedirectResponse(url="/admin", status_code=303)
    _set_session(response, username, secure=secure)
    return response


@admin_panel_router.get("/admin/logout")
async def logout(request: Request):
    session_id = request.cookies.get(SESSION_COOKIE)
    response = RedirectResponse(url="/admin/login", status_code=303)
    _clear_session(response, session_id)
    return response


# ---------------------------------------------------------------- change password


@admin_panel_router.get("/admin/change-password", response_class=HTMLResponse)
async def change_password_page(request: Request, msg: str = ""):
    username = _get_session(request)
    if not username:
        return RedirectResponse(url="/admin/login", status_code=303)

    flash = ""
    if msg:
        flash = f'<div class="flash warn">{_esc(msg)}</div>'
    csrf = request.cookies.get(SESSION_COOKIE, "")
    return HTMLResponse(content=CHANGE_PASSWORD_PAGE.format(
        sidebar=_nav_html("change-password", username),
        flash=flash,
        csrf_token=_esc(csrf),
    ))


@admin_panel_router.post("/admin/change-password", response_class=HTMLResponse)
async def change_password_submit(request: Request):
    username = _get_session(request)
    if not username:
        return RedirectResponse(url="/admin/login", status_code=303)

    form = await request.form()
    csrf_token = form.get("csrf_token", "")
    if not _csrf_check(request, csrf_token):
        return Response(status_code=401)

    csrf = _esc(request.cookies.get(SESSION_COOKIE, ""))
    current = form.get("current_password", "")
    new_pwd = form.get("new_password", "")
    confirm = form.get("confirm_password", "")

    if not isinstance(current, str) or not isinstance(new_pwd, str) or not isinstance(confirm, str):
        return HTMLResponse(
            content=CHANGE_PASSWORD_PAGE.format(
                sidebar=_nav_html("change-password", username),
                flash='<div class="flash err">表单数据无效</div>',
                csrf_token=csrf,
            ),
            status_code=400,
        )

    if not _verify_login(username, current):
        return HTMLResponse(
            content=CHANGE_PASSWORD_PAGE.format(
                sidebar=_nav_html("change-password", username),
                flash='<div class="flash err">当前密码不正确</div>',
                csrf_token=csrf,
            ),
            status_code=401,
        )

    if len(new_pwd) < 8:
        return HTMLResponse(
            content=CHANGE_PASSWORD_PAGE.format(
                sidebar=_nav_html("change-password", username),
                flash='<div class="flash err">新密码至少 8 位</div>',
                csrf_token=csrf,
            ),
            status_code=400,
        )

    if new_pwd != confirm:
        return HTMLResponse(
            content=CHANGE_PASSWORD_PAGE.format(
                sidebar=_nav_html("change-password", username),
                flash='<div class="flash err">两次输入的新密码不一致</div>',
                csrf_token=csrf,
            ),
            status_code=400,
        )

    if secrets.compare_digest(current, new_pwd):
        return HTMLResponse(
            content=CHANGE_PASSWORD_PAGE.format(
                sidebar=_nav_html("change-password", username),
                flash='<div class="flash err">新密码不能与当前密码相同</div>',
                csrf_token=csrf,
            ),
            status_code=400,
        )

    _save_creds(username, _hash_password(new_pwd, username))

    return HTMLResponse(
        content=CHANGE_PASSWORD_PAGE.format(
            sidebar=_nav_html("change-password", username),
            flash='<div class="flash">密码修改成功！<a href="/admin" style="color:var(--accent)">进入管理后台</a></div>',
            csrf_token=csrf,
        )
    )


# ---------------------------------------------------------------- dashboard


@admin_panel_router.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    username = _get_session(request)
    if not username:
        return RedirectResponse(url="/admin/login", status_code=303)

    if not _password_changed():
        return RedirectResponse(
            url="/admin/change-password?msg=首次登录，请修改默认密码",
            status_code=303,
        )

    msg = request.query_params.get("msg", "")
    msg_type = request.query_params.get("t", "ok")

    async with async_session_factory() as session:
        total_users = (
            await session.execute(select(func.count()).select_from(User))
        ).scalar() or 0

        active_subs_count = (
            await session.execute(
                select(func.count()).where(
                    Subscription.status == SubscriptionStatus.active,
                    Subscription.expires_at > utcnow(),
                )
            )
        ).scalar() or 0

        fulfilled_count = (
            await session.execute(
                select(func.count()).where(Order.status == OrderStatus.fulfilled)
            )
        ).scalar() or 0

        # Revenue by currency – SQL aggregation
        revenue_by_currency: dict[str, float] = {}
        currency_rows = (
            await session.execute(
                select(Order.currency, func.sum(Order.amount))
                .where(Order.status == OrderStatus.fulfilled, Order.amount > 0)
                .group_by(Order.currency)
            )
        ).all()
        for currency, total in currency_rows:
            revenue_by_currency[currency] = float(total or 0)

        # Revenue by provider + currency – SQL aggregation
        revenue_by_provider: dict[str, dict[str, float]] = {}
        provider_rows = (
            await session.execute(
                select(Order.provider, Order.currency, func.sum(Order.amount))
                .where(Order.status == OrderStatus.fulfilled, Order.amount > 0)
                .group_by(Order.provider, Order.currency)
            )
        ).all()
        for provider, currency, total in provider_rows:
            prov_key = provider.value if hasattr(provider, 'value') else str(provider)
            sub = revenue_by_provider.setdefault(prov_key, {})
            sub[currency] = float(total or 0)

        orders_7d = (
            await session.execute(
                select(func.count()).where(
                    Order.created_at >= utcnow() - timedelta(days=7),
                )
            )
        ).scalar() or 0

        plans = (await session.execute(select(Plan))).scalars().all()

        recent_orders = (
            await session.execute(
                select(Order).order_by(Order.created_at.desc()).limit(20)
            )
        ).scalars().all()

    plan_names = {p.id: p.name for p in plans}
    csrf_token = request.cookies.get(SESSION_COOKIE, "")

    flash = ""
    if msg:
        cls = "flash err" if msg_type == "err" else "flash"
        flash = f'<div class="{cls}">{_esc(msg)}</div>'

    revenue_cards = "".join(
        f'<div class="card"><div class="label">收入 {_esc(cur)}</div>'
        f'<div class="value">{amt:,.2f}</div></div>'
        for cur, amt in sorted(revenue_by_currency.items())
    )

    if revenue_by_provider:
        rows = "".join(
            f"<tr><td>{_esc(PROVIDER_LABELS.get(prov, prov))}</td>"
            f"<td>{', '.join(f'{amt:,.2f} {_esc(cur)}' for cur, amt in currencies.items())}</td></tr>"
            for prov, currencies in revenue_by_provider.items()
        )
        provider_table = f"<table><tr><th>渠道</th><th>累计收入</th></tr>{rows}</table>"
    else:
        provider_table = '<table><tr><td class="empty">暂无收入</td></tr></table>'

    if recent_orders:
        rows = "".join(
            f"<tr><td>{_esc(o.id[:8])}</td><td>{o.user_id}</td>"
            f"<td>{_esc(plan_names.get(o.plan_id, o.plan_id))}</td>"
            f"<td>{_esc(PROVIDER_LABELS.get(o.provider.value, o.provider.value))}</td>"
            f"<td>{o.amount:g} {_esc(o.currency)}</td>"
            f"<td><span class=\"tag {_esc(o.status.value)}\">{_esc(o.status.value)}</span>"
            + ('<span class="tag pending" title="超时关闭后又收到支付,已自动补发">迟付复活</span>' if getattr(o, "revived", False) else "")
            + f"</td>"
            f"<td>{o.created_at.strftime('%m-%d %H:%M') if o.created_at else '-'}</td></tr>"
            for o in recent_orders
        )
        orders_table = (
            "<table><tr><th>订单</th><th>用户</th><th>套餐</th><th>渠道</th>"
            "<th>金额</th><th>状态</th><th>时间</th></tr>" + rows + "</table>"
        )
    else:
        orders_table = '<table><tr><td class="empty">暂无订单</td></tr></table>'

    page = PAGE_TEMPLATE.format(
        generated_at=utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        sidebar=_nav_html("dashboard", username),
        flash=flash,
        csrf_token=_esc(csrf_token),
        total_users=total_users,
        active_subs=active_subs_count,
        fulfilled_orders=fulfilled_count,
        orders_7d=orders_7d,
        revenue_cards=revenue_cards,
        provider_table=provider_table,
        orders_table=orders_table,
    )

    return HTMLResponse(content=page)


# ---------------------------------------------------------------- plans page


@admin_panel_router.get("/admin/plans", response_class=HTMLResponse)
async def plans_page(request: Request):
    username = _get_session(request)
    if not username:
        return RedirectResponse(url="/admin/login", status_code=303)
    if not _password_changed():
        return RedirectResponse(
            url="/admin/change-password?msg=首次登录，请修改默认密码",
            status_code=303,
        )

    msg = request.query_params.get("msg", "")
    msg_type = request.query_params.get("t", "ok")
    csrf_token = request.cookies.get(SESSION_COOKIE, "")

    async with async_session_factory() as session:
        plans = (await session.execute(select(Plan))).scalars().all()
        bot_chats = (
            await session.execute(
                select(BotChat)
                .where(BotChat.is_member == True)  # noqa: E712
                .order_by(BotChat.is_admin.desc(), BotChat.updated_at.desc())
            )
        ).scalars().all()

    flash = ""
    if msg:
        cls = "flash err" if msg_type == "err" else "flash"
        flash = f'<div class="{cls}">{_esc(msg)}</div>'

    if plans:
        rows = []
        for p in plans:
            toggle_label = "停售" if p.is_active else "恢复"
            rows.append(
                f"<tr><td>{p.id}</td>"
                f"<td><form class='inline' method='post' action='/admin/plans/update'>"
                f"<input type='hidden' name='csrf_token' value='{csrf_token}'>"
                f"<input type='hidden' name='plan_id' value='{p.id}'>"
                f"<input class='w130' name='name' value='{_esc(p.name)}'>"
                f"<input class='w60' name='duration_days' type='number' min='1' value='{p.duration_days}'>"
                f"<input type='hidden' name='chat_id' value='{p.chat_id}'>"
                f"<span style='color:var(--muted);font-size:12px;display:inline-block;width:180px;vertical-align:middle;padding:0 6px' title='群组不可修改'>群组: {p.chat_id}</span>"
                f"<input class='w60' name='price_stars' type='number' value='{p.price_stars}' title='Stars·XTR | 0=不启用'>"
                f"<input class='w60' name='price_crypto' type='number' step='0.01' value='{p.price_crypto:g}' title='USDT | 0=不启用'>"
                f"<input class='w60' name='price_stripe' type='number' value='{p.price_stripe}' title='Stripe·美分(999=US$9.99) | 0=不启用'>"
                f"<input class='w80' name='price_alipay' type='number' step='0.01' value='{p.price_alipay:g}' title='支付宝·CNY | 0=不启用'>"
                f"<input class='w80' name='price_wechat' type='number' step='0.01' value='{p.price_wechat:g}' title='微信·CNY | 0=不启用'>"
                f"<button type='submit'>保存</button></form></td>"
                f"<td><span class=\"tag {'active' if p.is_active else 'inactive'}\">"
                f"{'在售' if p.is_active else '停用'}</span></td>"
                f"<td><form class='inline' method='post' action='/admin/plans/toggle'>"
                f"<input type='hidden' name='csrf_token' value='{csrf_token}'>"
                f"<input type='hidden' name='plan_id' value='{p.id}'>"
                f"<button type='submit' class='danger'>{toggle_label}</button></form></td></tr>"
            )
        plans_table = (
            "<table><tr><th>ID</th><th>名称 / 天数 / 群ID / XTR / USDT / 美分 / 支付宝CNY / 微信CNY</th>"
            "<th>状态</th><th>操作</th></tr>" + "".join(rows) + "</table>"
        )
    else:
        plans_table = '<table><tr><td class="empty">暂无套餐,用上方表单创建</td></tr></table>'

    _CHAT_TYPE_LABELS = {"group": "群组", "supergroup": "超级群", "channel": "频道"}
    if bot_chats:
        rows = []
        for c in bot_chats:
            type_label = _CHAT_TYPE_LABELS.get(c.type or "", c.type or "-")
            admin_tag = (
                '<span class="tag active">管理员</span>'
                if c.is_admin
                else '<span class="tag inactive">非管理员</span>'
            )
            uname = f"@{_esc(c.username)}" if c.username else "—"
            rows.append(
                f"<tr><td>{_esc(c.title or '(未命名)')}</td>"
                f"<td>{_esc(type_label)}</td>"
                f"<td>{uname}</td>"
                f"<td><code>{c.chat_id}</code></td>"
                f"<td>{admin_tag}</td></tr>"
            )
        bot_chats_table = (
            "<table><tr><th>名称</th><th>类型</th><th>用户名</th>"
            "<th>chat_id</th><th>机器人权限</th></tr>" + "".join(rows) + "</table>"
        )
    else:
        bot_chats_table = (
            '<table><tr><td class="empty">机器人还没有加入任何群/频道。'
            '把机器人加入群组并设为管理员后，这里会自动出现。</td></tr></table>'
        )

    chat_options = "".join(
        f'<option value="{c.chat_id}">'
        f'{_esc(c.title or "(未命名)")}'
        f'{" @" + _esc(c.username) if c.username else ""}'
        f'{"" if c.is_admin else " · 非管理员"}</option>'
        for c in bot_chats
    )

    page = PLANS_PAGE.format(
        sidebar=_nav_html("plans", username),
        flash=flash,
        csrf_token=_esc(csrf_token),
        bot_chats_table=bot_chats_table,
        chat_options=chat_options,
        plans_table=plans_table,
    )
    return HTMLResponse(content=page)


# ---------------------------------------------------------------- plan actions


@admin_panel_router.post("/admin/plans/create")
async def plan_create(
    request: Request,
    csrf_token: str = Form(""),
    name: str = Form(...),
    duration_days: int = Form(...),
    chat_id: int = Form(...),
    price_stars: int = Form(0),
    price_crypto: float = Form(0),
    price_stripe: int = Form(0),
    price_alipay: float = Form(0),
    price_wechat: float = Form(0),
):
    if not _get_session(request) or not _csrf_check(request, csrf_token) or not _password_changed():
        return Response(status_code=401)

    name = name.strip()[:255]
    if duration_days < 1:
        return _redirect("天数至少为 1", err=True, to="/admin/plans")

    # Validate the bot can actually access this chat
    try:
        from app.bot.dispatcher import bot

        await bot.get_chat(chat_id)
    except Exception:
        return _redirect(
            f"机器人未加入群组 {chat_id}，请先将机器人添加为该群管理员",
            err=True,
            to="/admin/plans",
        )

    async with async_session_factory() as session:
        plan = Plan(
            name=name,
            duration_days=duration_days,
            chat_id=chat_id,
            price_stars=price_stars,
            price_crypto=price_crypto,
            price_stripe=price_stripe,
            price_alipay=price_alipay,
            price_wechat=price_wechat,
        )
        session.add(plan)
        await session.commit()

    return _redirect(f"套餐「{name}」已创建", to="/admin/plans")


@admin_panel_router.post("/admin/plans/update")
async def plan_update(
    request: Request,
    csrf_token: str = Form(""),
    plan_id: int = Form(...),
    name: str = Form(...),
    duration_days: int = Form(...),
    chat_id: int = Form(...),
    price_stars: int = Form(0),
    price_crypto: float = Form(0),
    price_stripe: int = Form(0),
    price_alipay: float = Form(0),
    price_wechat: float = Form(0),
):
    if not _get_session(request) or not _csrf_check(request, csrf_token) or not _password_changed():
        return Response(status_code=401)

    name = name.strip()[:255]
    if duration_days < 1:
        return _redirect("天数至少为 1", err=True, to="/admin/plans")

    async with async_session_factory() as session:
        plan = await session.get(Plan, plan_id)
        if not plan:
            return _redirect("套餐不存在", err=True, to="/admin/plans")

        # Reject chat_id changes — existing subscriptions are tied to the
        # original chat_id and cannot be migrated automatically.  Create a
        # new plan instead.
        if str(chat_id) != str(plan.chat_id):
            return _redirect(
                f"不允许修改群组 ID（已有订阅绑定到群组 {plan.chat_id}）。"
                f"如需更换群组，请新建套餐。",
                err=True,
                to="/admin/plans",
            )

        plan.name = name
        plan.duration_days = duration_days
        plan.price_stars = price_stars
        plan.price_crypto = price_crypto
        plan.price_stripe = price_stripe
        plan.price_alipay = price_alipay
        plan.price_wechat = price_wechat
        await session.commit()

    return _redirect(f"套餐 #{plan_id} 已保存", to="/admin/plans")


@admin_panel_router.post("/admin/plans/toggle")
async def plan_toggle(
    request: Request,
    csrf_token: str = Form(""),
    plan_id: int = Form(...),
):
    if not _get_session(request) or not _csrf_check(request, csrf_token) or not _password_changed():
        return Response(status_code=401)

    async with async_session_factory() as session:
        plan = await session.get(Plan, plan_id)
        if not plan:
            return _redirect("套餐不存在", err=True, to="/admin/plans")
        plan.is_active = not plan.is_active
        state = "恢复在售" if plan.is_active else "已停售"
        await session.commit()

    return _redirect(f"套餐 #{plan_id} {state}", to="/admin/plans")


@admin_panel_router.post("/admin/subs/grant")
async def sub_grant(
    request: Request,
    csrf_token: str = Form(""),
    user_id: str = Form(...),
    plan_id: int = Form(...),
    days: int = Form(...),
):
    if not _get_session(request) or not _csrf_check(request, csrf_token) or not _password_changed():
        return Response(status_code=401)

    async with async_session_factory() as session:
        try:
            resolved_id = await resolve_telegram_user_id(session, user_id)
        except ValueError as e:
            return _redirect(str(e), err=True, to="/admin/members")
        try:
            from app.services.grant import grant_subscription

            order = await grant_subscription(session, resolved_id, plan_id, days)
            plan = await session.get(Plan, plan_id)
            user = await session.get(User, resolved_id)
            await session.commit()
        except ValueError as e:
            return _redirect(str(e), err=True, to="/admin/members")

    from app.services.notify import notify_fulfillment

    result = await notify_fulfillment(order.id)
    plan_name = plan.name if plan else str(plan_id)
    label = format_user_ref(resolved_id, user.username if user else None)
    if not result.link:
        return _redirect(
            "会员已赠送,但邀请链接创建失败——请确保机器人是该群管理员",
            err=True,
            to="/admin/members",
        )
    if result.dm_sent:
        return _redirect(
            f"已赠送 {days} 天「{plan_name}」给用户 {label}，邀请链接已发送至 Telegram",
            to="/admin/members",
        )
    return _redirect(
        f"已赠送 {days} 天「{plan_name}」给用户 {label}。"
        f"对方未与机器人对话，无法私聊发送。请手动转发入群链接：{result.link}",
        to="/admin/members",
    )


# ---------------------------------------------------------------- sub actions


@admin_panel_router.post("/admin/subs/adjust")
async def sub_adjust(
    request: Request,
    csrf_token: str = Form(""),
    sub_id: int = Form(...),
    delta: str = Form(...),
):
    if not _get_session(request) or not _csrf_check(request, csrf_token) or not _password_changed():
        return Response(status_code=401)

    delta = delta.strip()
    async with async_session_factory() as session:
        sub = await session.get(Subscription, sub_id)
        if not sub:
            return _redirect("订阅不存在", err=True, to="/admin/members")
        try:
            sub.expires_at = apply_expiry_delta(sub.expires_at, delta)
        except ValueError:
            return _redirect("无效的调整值,用 +7 / -3 / YYYY-MM-DD", err=True, to="/admin/members")
        sub.last_reminded_at = None
        new_expiry = sub.expires_at
        user_id = sub.user_id
        chat_id = sub.group_chat_id
        await session.commit()

    if new_expiry <= utcnow():
        from app.services.kick import kick_user_from_chat

        async with async_session_factory() as session:
            sub = await session.get(Subscription, sub_id)
            if sub:
                if await kick_user_from_chat(chat_id, user_id):
                    sub.status = SubscriptionStatus.kicked
                await session.commit()

    try:
        from app.bot.dispatcher import bot

        await bot.send_message(
            user_id,
            f"Your subscription expiry has been updated to "
            f"<b>{new_expiry.strftime('%Y-%m-%d %H:%M UTC')}</b>.",
        )
    except Exception as e:
        logger.warning("Adjust expiry notify failed user=%d: %s", user_id, e)

    return _redirect(
        f"用户 {user_id} 到期时间已调整为 {new_expiry.strftime('%Y-%m-%d %H:%M')}",
        to="/admin/members",
    )


@admin_panel_router.post("/admin/subs/revoke")
async def sub_revoke(
    request: Request,
    csrf_token: str = Form(""),
    sub_id: int = Form(...),
):
    if not _get_session(request) or not _csrf_check(request, csrf_token) or not _password_changed():
        return Response(status_code=401)

    async with async_session_factory() as session:
        sub = await session.get(Subscription, sub_id)
        if not sub:
            return _redirect("订阅不存在", err=True, to="/admin/members")
        sub.status = SubscriptionStatus.kicked
        user_id, chat_id = sub.user_id, sub.group_chat_id
        await session.commit()

    try:
        from app.bot.dispatcher import bot

        await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
        await bot.unban_chat_member(chat_id=chat_id, user_id=user_id)
    except Exception as e:
        logger.error("Panel revoke: failed to kick user %d: %s", user_id, e)
        return _redirect(
            f"订阅已标记移除,但踢人失败(检查机器人权限): {e}",
            err=True,
            to="/admin/members",
        )

    return _redirect(f"用户 {user_id} 已移除并踢出群", to="/admin/members")


# ---------------------------------------------------------------- members page


MEMBERS_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>会员管理 — 管理后台</title>
""" + _HEAD_ASSETS + """
</head>
<body class="layout-body">
<div class="layout">
{sidebar}
<main class="main-content">
<div class="page-hdr">
  <div>
    <div class="page-title">会员管理</div>
    <div class="page-sub">赠送、搜索、导出与优惠链接</div>
  </div>
</div>
{flash}

<h2>赠送会员</h2>
<form method="post" action="/admin/subs/grant" style="margin:0">
<input type="hidden" name="csrf_token" value="{csrf_token}">
<div class="formrow">
  <span>用户:</span>
  <input class="w130" name="user_id" type="text" placeholder="ID 或 @用户名" required>
  <span>套餐:</span>
  <select name="plan_id" style="width:180px">{grant_options}</select>
  <span>天数:</span>
  <input class="w60" name="days" type="number" min="1" placeholder="天数" required>
  <button type="submit">赠送</button>
</div>
</form>

<h2>活跃会员</h2>
<div class="formrow">
  <form class="inline" method="get" action="/admin/members">
    <span>搜索用户:</span>
    <input class="w130" name="q" placeholder="ID 或 @用户名" value="{q}">
    <button type="submit">搜索</button>
  </form>
  <a class="btn-save" href="/admin/members/export" style="text-decoration:none;display:inline-block;padding:8px 14px">导出 CSV</a>
</div>
{subs_table}

<h2>免费体验邀请链接</h2>
<p style="color:var(--muted);font-size:13px;margin:0 0 10px">用户通过链接申请入群后，自动获得下方天数会员；修改天数只影响之后新入群用户。新用户=该群从未有过会员记录，老用户=曾有过。</p>
<form method="post" action="/admin/promos/trial/create" style="margin:0 0 12px">
<input type="hidden" name="csrf_token" value="{csrf_token}">
<div class="formrow" style="flex-wrap:wrap">
  <input class="w130" name="name" placeholder="活动名称" required>
  <select name="plan_id" style="width:180px">{grant_options}</select>
  <select name="audience" style="width:120px" title="适用用户">
    <option value="all">全部用户</option>
    <option value="new">仅新用户</option>
    <option value="returning">仅老用户</option>
  </select>
  <input class="w60" name="grant_days" type="number" min="1" placeholder="天数" required>
  <input class="w60" name="max_uses" type="number" min="0" placeholder="上限" value="0" title="0=不限">
  <input class="w160" name="link_expire_at" type="datetime-local" title="链接过期时间（可选）">
  <button type="submit">创建体验链接</button>
</div>
</form>
{trial_table}

<h2>付费折扣链接</h2>
<p style="color:var(--muted);font-size:13px;margin:0 0 10px">用户打开 <code>t.me/Bot?start=promo_xxx</code> 后购买对应套餐按折扣计费；改折扣只影响之后新订单。可限制仅新用户或仅老用户。</p>
<form method="post" action="/admin/promos/discount/create" style="margin:0 0 12px">
<input type="hidden" name="csrf_token" value="{csrf_token}">
<div class="formrow" style="flex-wrap:wrap">
  <input class="w130" name="name" placeholder="活动名称" required>
  <select name="plan_id" style="width:180px">{grant_options}</select>
  <select name="audience" style="width:120px" title="适用用户">
    <option value="all">全部用户</option>
    <option value="new">仅新用户</option>
    <option value="returning">仅老用户</option>
  </select>
  <input class="w60" name="discount_percent" type="number" min="0" max="99" placeholder="%OFF" value="0">
  <input class="w80" name="discount_amount" type="number" step="0.01" min="0" placeholder="减免额" value="0" title="百分比优先；都填0无效">
  <input class="w60" name="max_uses" type="number" min="0" placeholder="上限" value="0" title="0=不限">
  <button type="submit">创建折扣链接</button>
</div>
</form>
{discount_table}

</main>
</div>
""" + _THEME_SCRIPT + """
</body>
</html>"""


def _parse_optional_datetime(raw: str):
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            from datetime import datetime as _dt
            return _dt.strptime(raw, fmt)
        except ValueError:
            continue
    return None


@admin_panel_router.get("/admin/members", response_class=HTMLResponse)
async def members_page(request: Request):
    username = _get_session(request)
    if not username:
        return RedirectResponse(url="/admin/login", status_code=303)
    if not _password_changed():
        return RedirectResponse(
            url="/admin/change-password?msg=首次登录，请修改默认密码",
            status_code=303,
        )

    q = request.query_params.get("q", "").strip()
    msg = request.query_params.get("msg", "")
    msg_type = request.query_params.get("t", "ok")
    csrf_token = request.cookies.get(SESSION_COOKIE, "")

    async with async_session_factory() as session:
        plans = (await session.execute(select(Plan))).scalars().all()
        plan_names = {p.id: p.name for p in plans}

        sub_stmt = select(Subscription).where(
            Subscription.status == SubscriptionStatus.active,
            Subscription.expires_at > utcnow(),
        )
        if q:
            user_ids = await find_user_ids_for_search(session, q)
            if user_ids:
                sub_stmt = sub_stmt.where(Subscription.user_id.in_(user_ids))
            else:
                sub_stmt = sub_stmt.where(Subscription.user_id == -1)
        subs = (
            (await session.execute(sub_stmt.order_by(Subscription.expires_at).limit(100)))
            .scalars()
            .all()
        )
        sub_user_ids = {s.user_id for s in subs}
        sub_users = (
            await session.execute(select(User).where(User.id.in_(sub_user_ids)))
        ).scalars().all() if sub_user_ids else []
        user_labels = {u.id: format_user_ref(u.id, u.username) for u in sub_users}

        from app.services.promo import list_promos
        trials = await list_promos(session, kind=PromoKind.trial)
        discounts = await list_promos(session, kind=PromoKind.discount)

    flash = ""
    if msg:
        cls = "flash err" if msg_type == "err" else "flash"
        flash = f'<div class="{cls}">{_esc(msg)}</div>'

    grant_options = "".join(
        f'<option value="{p.id}">{_esc(p.name)} (ID:{p.id}, {p.duration_days}天)</option>'
        for p in plans if p.is_active
    ) or '<option value="">无可用套餐</option>'

    if subs:
        now = utcnow()
        rows = []
        for s in subs:
            rows.append(
                f"<tr><td>{_esc(user_labels.get(s.user_id, str(s.user_id)))}</td>"
                f"<td>{_esc(plan_names.get(s.plan_id, s.plan_id))}</td>"
                f"<td>{s.expires_at.strftime('%Y-%m-%d %H:%M')}</td>"
                f"<td>{max((s.expires_at - now).days, 0)} 天</td>"
                f"<td><form class='inline' method='post' action='/admin/subs/adjust'>"
                f"<input type='hidden' name='csrf_token' value='{csrf_token}'>"
                f"<input type='hidden' name='sub_id' value='{s.id}'>"
                f"<input class='w90' name='delta' placeholder='+7 / -3' required>"
                f"<button type='submit'>调整</button></form> "
                f"<form class='inline' method='post' action='/admin/subs/revoke' "
                f"onsubmit=\"return confirm('确认移除该会员并踢出群?')\">"
                f"<input type='hidden' name='csrf_token' value='{csrf_token}'>"
                f"<input type='hidden' name='sub_id' value='{s.id}'>"
                f"<button type='submit' class='danger'>移除</button></form></td></tr>"
            )
        subs_table = (
            "<table><tr><th>用户</th><th>套餐</th><th>到期 (UTC)</th>"
            "<th>剩余</th><th>操作</th></tr>" + "".join(rows) + "</table>"
        )
    else:
        hint = f"未找到用户 {_esc(q)} 的活跃订阅" if q else "暂无活跃订阅"
        subs_table = f'<table><tr><td class="empty">{hint}</td></tr></table>'

    trial_rows = []
    from app.services.promo import AUDIENCE_LABELS, parse_audience
    for p in trials:
        uses = f"{p.used_count}/{p.max_uses}" if p.max_uses else f"{p.used_count}/∞"
        status = "启用" if p.is_active else "停用"
        link = _esc(p.invite_link or "")
        aud = AUDIENCE_LABELS.get(
            p.audience if isinstance(p.audience, PromoAudience) else parse_audience(str(getattr(p.audience, "value", p.audience))),
            "全部用户",
        )
        trial_rows.append(
            f"<tr><td>{p.id}</td><td>{_esc(p.name)}</td>"
            f"<td>{_esc(plan_names.get(p.plan_id, p.plan_id))}</td>"
            f"<td>{_esc(aud)}</td>"
            f"<td><form class='inline' method='post' action='/admin/promos/trial/update'>"
            f"<input type='hidden' name='csrf_token' value='{csrf_token}'>"
            f"<input type='hidden' name='promo_id' value='{p.id}'>"
            f"<input class='w60' name='grant_days' type='number' min='1' value='{p.grant_days}'>"
            f"<button type='submit'>改天数</button></form></td>"
            f"<td>{uses}</td><td>{status}</td>"
            f"<td style='max-width:220px;word-break:break-all'><code>{link}</code></td>"
            f"<td><form class='inline' method='post' action='/admin/promos/toggle'>"
            f"<input type='hidden' name='csrf_token' value='{csrf_token}'>"
            f"<input type='hidden' name='promo_id' value='{p.id}'>"
            f"<button type='submit'>{'停用' if p.is_active else '启用'}</button></form> "
            f"<form class='inline' method='post' action='/admin/promos/trial/revoke' "
            f"onsubmit=\"return confirm('撤销该邀请链接?')\">"
            f"<input type='hidden' name='csrf_token' value='{csrf_token}'>"
            f"<input type='hidden' name='promo_id' value='{p.id}'>"
            f"<button type='submit' class='danger'>撤销链接</button></form></td></tr>"
        )
    trial_table = (
        "<table><tr><th>ID</th><th>名称</th><th>套餐</th><th>适用</th><th>体验天数</th>"
        "<th>已用</th><th>状态</th><th>链接</th><th>操作</th></tr>"
        + ("".join(trial_rows) if trial_rows else '<tr><td class="empty" colspan="9">暂无体验链接</td></tr>')
        + "</table>"
    )

    disc_rows = []
    for p in discounts:
        uses = f"{p.used_count}/{p.max_uses}" if p.max_uses else f"{p.used_count}/∞"
        status = "启用" if p.is_active else "停用"
        if p.discount_percent:
            disc_label = f"{p.discount_percent}% OFF"
        elif p.discount_amount:
            disc_label = f"-{p.discount_amount:g}"
        else:
            disc_label = "—"
        payload = _esc(p.start_payload or "")
        aud = AUDIENCE_LABELS.get(
            p.audience if isinstance(p.audience, PromoAudience) else parse_audience(str(getattr(p.audience, "value", p.audience))),
            "全部用户",
        )
        disc_rows.append(
            f"<tr><td>{p.id}</td><td>{_esc(p.name)}</td>"
            f"<td>{_esc(plan_names.get(p.plan_id, p.plan_id))}</td>"
            f"<td>{_esc(aud)}</td>"
            f"<td><form class='inline' method='post' action='/admin/promos/discount/update'>"
            f"<input type='hidden' name='csrf_token' value='{csrf_token}'>"
            f"<input type='hidden' name='promo_id' value='{p.id}'>"
            f"<input class='w60' name='discount_percent' type='number' min='0' max='99' value='{p.discount_percent}'>"
            f"<input class='w80' name='discount_amount' type='number' step='0.01' min='0' value='{p.discount_amount:g}'>"
            f"<button type='submit'>改折扣</button></form><div style='font-size:12px;color:var(--muted)'>{_esc(disc_label)}</div></td>"
            f"<td>{uses}</td><td>{status}</td>"
            f"<td><code>?start={payload}</code></td>"
            f"<td><form class='inline' method='post' action='/admin/promos/toggle'>"
            f"<input type='hidden' name='csrf_token' value='{csrf_token}'>"
            f"<input type='hidden' name='promo_id' value='{p.id}'>"
            f"<button type='submit'>{'停用' if p.is_active else '启用'}</button></form></td></tr>"
        )
    discount_table = (
        "<table><tr><th>ID</th><th>名称</th><th>套餐</th><th>适用</th><th>折扣</th>"
        "<th>已用</th><th>状态</th><th>start 参数</th><th>操作</th></tr>"
        + ("".join(disc_rows) if disc_rows else '<tr><td class="empty" colspan="9">暂无折扣链接</td></tr>')
        + "</table>"
    )

    page = MEMBERS_PAGE.format(
        sidebar=_nav_html("members", username),
        flash=flash,
        csrf_token=_esc(csrf_token),
        q=_esc(q),
        grant_options=grant_options,
        subs_table=subs_table,
        trial_table=trial_table,
        discount_table=discount_table,
    )
    return HTMLResponse(content=page)


@admin_panel_router.get("/admin/members/export")
async def members_export(request: Request):
    username = _get_session(request)
    if not username or not _password_changed():
        return Response(status_code=401)

    import csv
    import io

    buf = io.StringIO()
    # UTF-8 BOM for Excel
    buf.write("\ufeff")
    writer = csv.writer(buf)
    writer.writerow([
        "user_id", "username", "plan_name", "group_chat_id", "expires_at", "status",
        "order_id", "provider", "amount", "currency", "order_created_at",
    ])

    async with async_session_factory() as session:
        subs = (
            await session.execute(
                select(Subscription)
                .where(
                    Subscription.status == SubscriptionStatus.active,
                    Subscription.expires_at > utcnow(),
                )
                .order_by(Subscription.expires_at)
            )
        ).scalars().all()
        plan_ids = {s.plan_id for s in subs}
        order_ids = {s.order_id for s in subs}
        user_ids = {s.user_id for s in subs}
        plans = (
            await session.execute(select(Plan).where(Plan.id.in_(plan_ids)))
        ).scalars().all() if plan_ids else []
        orders = (
            await session.execute(select(Order).where(Order.id.in_(order_ids)))
        ).scalars().all() if order_ids else []
        users = (
            await session.execute(select(User).where(User.id.in_(user_ids)))
        ).scalars().all() if user_ids else []
        plan_map = {p.id: p for p in plans}
        order_map = {o.id: o for o in orders}
        user_map = {u.id: u for u in users}

        for s in subs:
            plan = plan_map.get(s.plan_id)
            order = order_map.get(s.order_id)
            user = user_map.get(s.user_id)
            writer.writerow([
                s.user_id,
                user.username if user and user.username else "",
                plan.name if plan else s.plan_id,
                s.group_chat_id,
                s.expires_at.strftime("%Y-%m-%d %H:%M:%S") if s.expires_at else "",
                s.status.value if hasattr(s.status, "value") else s.status,
                s.order_id,
                (order.provider.value if order and hasattr(order.provider, "value") else (order.provider if order else "")),
                order.amount if order else "",
                order.currency if order else "",
                order.created_at.strftime("%Y-%m-%d %H:%M:%S") if order and order.created_at else "",
            ])

    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="members_export.csv"',
        },
    )


@admin_panel_router.post("/admin/promos/trial/create")
async def promo_trial_create(
    request: Request,
    csrf_token: str = Form(""),
    name: str = Form(...),
    plan_id: int = Form(...),
    audience: str = Form("all"),
    grant_days: int = Form(...),
    max_uses: int = Form(0),
    link_expire_at: str = Form(""),
):
    if not _get_session(request) or not _csrf_check(request, csrf_token) or not _password_changed():
        return Response(status_code=401)
    if grant_days < 1:
        return _redirect("体验天数至少为 1", err=True, to="/admin/members")

    expire = _parse_optional_datetime(link_expire_at)
    from app.services.promo import parse_audience
    audience_val = parse_audience(audience)

    async with async_session_factory() as session:
        plan = await session.get(Plan, plan_id)
        if not plan or not plan.is_active:
            return _redirect("套餐不存在或已停用", err=True, to="/admin/members")

        promo = PromoCampaign(
            name=name.strip() or "体验活动",
            kind=PromoKind.trial,
            plan_id=plan_id,
            audience=audience_val,
            grant_days=grant_days,
            max_uses=max(0, max_uses),
            link_expire_at=expire,
            is_active=True,
        )
        session.add(promo)
        await session.flush()
        invite_name = f"promo_{promo.id}"
        promo.invite_link_name = invite_name
        await session.commit()
        chat_id = plan.chat_id
        promo_id = promo.id

    try:
        from app.services.invites import create_join_request_invite

        link = await create_join_request_invite(
            chat_id, name=invite_name, expire_date=expire
        )
    except Exception as e:
        async with async_session_factory() as session:
            promo = await session.get(PromoCampaign, promo_id)
            if promo:
                promo.is_active = False
                await session.commit()
        return _redirect(f"创建邀请链接失败: {e}", err=True, to="/admin/members")

    async with async_session_factory() as session:
        promo = await session.get(PromoCampaign, promo_id)
        if promo:
            promo.invite_link = link
            await session.commit()

    return _redirect(f"体验链接已创建：{link}", to="/admin/members")


@admin_panel_router.post("/admin/promos/trial/update")
async def promo_trial_update(
    request: Request,
    csrf_token: str = Form(""),
    promo_id: int = Form(...),
    grant_days: int = Form(...),
):
    if not _get_session(request) or not _csrf_check(request, csrf_token) or not _password_changed():
        return Response(status_code=401)
    if grant_days < 1:
        return _redirect("体验天数至少为 1", err=True, to="/admin/members")
    async with async_session_factory() as session:
        promo = await session.get(PromoCampaign, promo_id)
        if not promo or promo.kind != PromoKind.trial:
            return _redirect("活动不存在", err=True, to="/admin/members")
        promo.grant_days = grant_days
        await session.commit()
    return _redirect(f"体验天数已更新为 {grant_days} 天", to="/admin/members")


@admin_panel_router.post("/admin/promos/trial/revoke")
async def promo_trial_revoke(
    request: Request,
    csrf_token: str = Form(""),
    promo_id: int = Form(...),
):
    if not _get_session(request) or not _csrf_check(request, csrf_token) or not _password_changed():
        return Response(status_code=401)

    async with async_session_factory() as session:
        promo = await session.get(PromoCampaign, promo_id)
        if not promo or promo.kind != PromoKind.trial:
            return _redirect("活动不存在", err=True, to="/admin/members")
        plan = await session.get(Plan, promo.plan_id)
        link = promo.invite_link
        chat_id = plan.chat_id if plan else None
        promo.is_active = False
        await session.commit()

    if chat_id and link:
        try:
            from app.services.invites import revoke_invite_link
            await revoke_invite_link(chat_id, link)
        except Exception as e:
            logger.warning("Revoke invite link failed promo=%s: %s", promo_id, e)
            return _redirect(f"已停用活动，但撤销 Telegram 链接失败: {e}", err=True, to="/admin/members")

    return _redirect("体验链接已撤销", to="/admin/members")


@admin_panel_router.post("/admin/promos/discount/create")
async def promo_discount_create(
    request: Request,
    csrf_token: str = Form(""),
    name: str = Form(...),
    plan_id: int = Form(...),
    audience: str = Form("all"),
    discount_percent: int = Form(0),
    discount_amount: float = Form(0),
    max_uses: int = Form(0),
):
    if not _get_session(request) or not _csrf_check(request, csrf_token) or not _password_changed():
        return Response(status_code=401)
    if discount_percent <= 0 and discount_amount <= 0:
        return _redirect("请填写折扣百分比或固定减免", err=True, to="/admin/members")
    if discount_percent < 0 or discount_percent > 99:
        return _redirect("折扣百分比须在 0–99", err=True, to="/admin/members")

    from app.services.promo import make_start_payload, parse_audience
    audience_val = parse_audience(audience)

    async with async_session_factory() as session:
        plan = await session.get(Plan, plan_id)
        if not plan or not plan.is_active:
            return _redirect("套餐不存在或已停用", err=True, to="/admin/members")
        payload = make_start_payload()
        promo = PromoCampaign(
            name=name.strip() or "折扣活动",
            kind=PromoKind.discount,
            plan_id=plan_id,
            audience=audience_val,
            discount_percent=int(discount_percent or 0),
            discount_amount=float(discount_amount or 0),
            max_uses=max(0, max_uses),
            start_payload=payload,
            is_active=True,
        )
        session.add(promo)
        await session.commit()
        payload_saved = payload

    bot_username = ""
    try:
        from app.bot.dispatcher import bot
        me = await bot.get_me()
        bot_username = me.username or ""
    except Exception:
        pass
    link = (
        f"https://t.me/{bot_username}?start={payload_saved}"
        if bot_username
        else f"?start={payload_saved}"
    )
    return _redirect(f"折扣链接已创建：{link}", to="/admin/members")


@admin_panel_router.post("/admin/promos/discount/update")
async def promo_discount_update(
    request: Request,
    csrf_token: str = Form(""),
    promo_id: int = Form(...),
    discount_percent: int = Form(0),
    discount_amount: float = Form(0),
):
    if not _get_session(request) or not _csrf_check(request, csrf_token) or not _password_changed():
        return Response(status_code=401)
    if discount_percent <= 0 and discount_amount <= 0:
        return _redirect("请填写折扣百分比或固定减免", err=True, to="/admin/members")
    async with async_session_factory() as session:
        promo = await session.get(PromoCampaign, promo_id)
        if not promo or promo.kind != PromoKind.discount:
            return _redirect("活动不存在", err=True, to="/admin/members")
        promo.discount_percent = int(discount_percent or 0)
        promo.discount_amount = float(discount_amount or 0)
        await session.commit()
    return _redirect("折扣已更新", to="/admin/members")


@admin_panel_router.post("/admin/promos/toggle")
async def promo_toggle(
    request: Request,
    csrf_token: str = Form(""),
    promo_id: int = Form(...),
):
    if not _get_session(request) or not _csrf_check(request, csrf_token) or not _password_changed():
        return Response(status_code=401)
    async with async_session_factory() as session:
        promo = await session.get(PromoCampaign, promo_id)
        if not promo:
            return _redirect("活动不存在", err=True, to="/admin/members")
        promo.is_active = not promo.is_active
        state = "已启用" if promo.is_active else "已停用"
        await session.commit()
    return _redirect(f"活动 #{promo_id} {state}", to="/admin/members")


# ---------------------------------------------------------------- settings page

SETTINGS_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>支付设置 — 管理后台</title>
""" + _HEAD_ASSETS + """
</head>
<body class="layout-body">
<div class="layout">
{sidebar}
<main class="main-content">
<div class="page-hdr">
  <h1>支付设置</h1>
  <div class="sub">修改后即时生效，无需重启</div>
</div>
{flash}

<form method="post" action="/admin/settings/save">
<input type="hidden" name="csrf_token" value="{csrf_token}">

{groups}

<div class="formrow" style="justify-content:center; margin-top:20px;">
  <button type="submit" style="padding:10px 40px; font-size:14px;">保存所有设置</button>
</div>
</form>

</main>
</div>
<script>
(function () {{
  function updateBackendSections() {{
    var alipay = document.querySelector('[name="alipay_backend"]');
    var wechat = document.querySelector('[name="wechat_backend"]');
    var av = alipay ? alipay.value : '';
    var wv = wechat ? wechat.value : '';
    var epay = document.getElementById('backend-section-epay');
    var hpj = document.getElementById('backend-section-hupijiao');
    if (epay) epay.classList.toggle('hidden', av !== 'epay' && wv !== 'epay');
    if (hpj) hpj.classList.toggle('hidden', av !== 'hupijiao' && wv !== 'hupijiao');
  }}
  document.querySelectorAll('[name="alipay_backend"], [name="wechat_backend"]').forEach(function (el) {{
    el.addEventListener('change', updateBackendSections);
  }});
  updateBackendSections();
}})();
</script>
""" + _THEME_SCRIPT + """
</body>
</html>"""


def _render_settings_form(config: dict, csrf_token: str) -> str:
    """Build grouped form HTML from the current config.

    Routing groups only contain the backend selector. Epay / HuPiJiao config
    lives in dedicated shared sections (one copy each). Client-side JS toggles
    section visibility when routing dropdowns change.
    """
    from app.payment_config import BACKEND_SECTION_IDS, FIELD_META, GROUPS

    def _render_field(key: str, ftype: str, label: str, val: str, placeholder: str) -> str:
        if ftype == "bool":
            checked = "checked" if str(val).lower() in ("true", "on", "1", "yes") else ""
            return (
                f'<tr><td style="width:140px">{_esc(label)}</td>'
                f'<td><input type="hidden" name="{_esc(key)}" value="off">'
                f'<label style="cursor:pointer;display:inline-flex;align-items:center;gap:6px">'
                f'<input type="checkbox" name="{_esc(key)}" value="on" {checked}> 启用'
                f'</label></td></tr>'
            )
        if ftype == "select_epay_hupijiao":
            opts = [("", "禁用"), ("epay", "易支付 (Epay)"), ("hupijiao", "虎皮椒 (HuPiJiao)")]
            selected = str(val)
            sel_html = "".join(
                f'<option value="{_esc(v)}" {"selected" if v == selected else ""}>{_esc(lbl)}</option>'
                for v, lbl in opts
            )
            return (
                f'<tr><td style="width:140px">{_esc(label)}</td>'
                f'<td><select name="{_esc(key)}" style="width:180px">{sel_html}</select></td></tr>'
            )
        if ftype == "password":
            hint = f"{_esc(placeholder)}（留空保持不变）" if placeholder else "留空保持不变"
            return (
                f'<tr><td style="width:140px">{_esc(label)}</td>'
                f'<td><input type="password" name="{_esc(key)}" value="" '
                f'placeholder="{hint}" style="width:360px" autocomplete="new-password"></td></tr>'
            )
        return (
            f'<tr><td style="width:140px">{_esc(label)}</td>'
            f'<td><input type="text" name="{_esc(key)}" value="{_esc(val)}" '
            f'placeholder="{_esc(placeholder)}" style="width:360px"></td></tr>'
        )

    groups_html: list[str] = []

    for group_name in GROUPS:
        fields: list[str] = []
        for key, (group, label, placeholder, ftype) in FIELD_META.items():
            if group != group_name:
                continue
            val = config.get(key, "")
            fields.append(_render_field(key, ftype, label, val, placeholder))

        if not fields:
            continue

        rows = "".join(fields)
        section_id = BACKEND_SECTION_IDS.get(group_name, "")
        if section_id:
            # Shared backend config — single section, toggled by JS
            groups_html.append(
                f'<div id="{section_id}" class="backend-section">'
                f'<h2>{_esc(group_name)}</h2>'
                f'<p class="backend-hint">全局共用：支付宝/微信路由指向此后台时共用以下配置，只需填写一次。</p>'
                f'<table><tbody>{rows}</tbody></table>'
                f'</div>'
            )
        else:
            groups_html.append(
                f'<h2>{_esc(group_name)}</h2>'
                f'<table><tbody>{rows}</tbody></table>'
            )
            if group_name == "微信支付路由":
                    groups_html.append(
                        '<p class="backend-hint">'
                        '选择「易支付」或「虎皮椒」后，下方会自动显示对应配置区域。'
                        '支付宝与微信可分别选择不同后台，也可共用同一后台（只填一份配置）。'
                        '</p>'
                    )

    return "".join(groups_html)


@admin_panel_router.get("/admin/settings", response_class=HTMLResponse)
async def settings_page(request: Request, msg: str = "", msg_type: str = "ok"):
    username = _get_session(request)
    if not username or not _password_changed():
        return RedirectResponse(url="/admin/login", status_code=303)

    from app.payment_config import get_config

    flash = ""
    if msg:
        cls = "flash err" if msg_type == "err" else "flash"
        flash = f'<div class="{cls}">{_esc(msg)}</div>'

    csrf_token = request.cookies.get(SESSION_COOKIE, "")
    page = SETTINGS_PAGE.format(
        sidebar=_nav_html("settings", username),
        flash=flash,
        csrf_token=_esc(csrf_token),
        groups=_render_settings_form(get_config(), csrf_token),
    )
    return HTMLResponse(content=page)


@admin_panel_router.post("/admin/settings/save", response_class=HTMLResponse)
async def settings_save(request: Request):
    username = _get_session(request)
    if not username or not _password_changed():
        return RedirectResponse(url="/admin/login", status_code=303)

    form = await request.form()
    csrf_token = form.get("csrf_token", "")
    if not _csrf_check(request, csrf_token):
        return Response(status_code=401)

    from app.payment_config import save_config

    data = {k: v for k, v in form.items() if k != "csrf_token"}
    save_config(data)

    flash = '<div class="flash">支付设置已保存，即时生效</div>'
    csrf = request.cookies.get(SESSION_COOKIE, "")
    from app.payment_config import get_config

    return HTMLResponse(
        content=SETTINGS_PAGE.format(
            sidebar=_nav_html("settings", username),
            flash=flash,
            csrf_token=_esc(csrf),
            groups=_render_settings_form(get_config(), csrf),
        )
    )


# ---------------------------------------------------------------- bot config page

BOT_CONFIG_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>机器人配置 — 管理后台</title>
""" + _HEAD_ASSETS + """
</head>
<body class="layout-body">
<div class="layout">
{sidebar}
<main class="main-content">
<div class="page-hdr">
  <h1>⚙️ 机器人配置</h1>
  <div class="sub">修改后即时生效，无需重启</div>
</div>
{flash}

<form method="post" action="/admin/bot-config/save">
<input type="hidden" name="csrf_token" value="{csrf_token}">

<div class="sc">
  <h2>🤖 基础配置</h2>

  <div class="frow">
    <label>Bot Token <span class="req">*</span></label>
    <input type="password" name="bot_token" value=""
      placeholder="留空保持不变；修改后需重启服务" autocomplete="new-password">
    <div class="fhint" style="color:var(--orange)">⚠️ 修改 Bot Token 后需<strong>重启服务</strong>才会生效（其余配置即时生效）</div>
  </div>

  <div class="frow">
    <label>管理员 TG 用户名</label>
    <input type="text" name="admin_usernames" value="{admin_usernames}"
      placeholder="username1,username2（不含 @，逗号分隔）">
    <div class="fhint">逗号分隔，不含 @。与 .env 中 ADMIN_IDS 二选一或同时填写，保存后即时生效</div>
  </div>

  <div class="frow">
    <label>机器人欢迎语</label>
    <textarea name="welcome_message" rows="4">{welcome_message}</textarea>
    <div class="fhint">全新用户打开机器人时，Telegram 会自动显示 <strong>START</strong> 按钮，点一下即发送 /start 并触发欢迎语与底部菜单；老用户也可随时发 /start 刷新菜单</div>
  </div>

  <div class="frow">
    <label>订单超时时间（分钟）<span class="req">*</span></label>
    <input type="number" name="order_timeout_minutes" value="{order_timeout_minutes}"
      min="1" max="1440" style="max-width:180px">
    <div class="fhint">订单多久未支付将自动过期</div>
  </div>
</div>

<div class="sc">
  <h2>🔗 底部菜单入口链接</h2>

  <div class="frow">
    <label>VIP 群组链接</label>
    <input type="text" name="vip_group_url" value="{vip_group_url}"
      placeholder="https://t.me/+xxxx 或 https://t.me/组名">
    <div class="fhint">填写后，机器人底部菜单会显示「👥 VIP群组」按钮；留空则隐藏该按钮</div>
  </div>

  <div class="frow">
    <label>VIP 频道链接</label>
    <input type="text" name="vip_channel_url" value="{vip_channel_url}"
      placeholder="https://t.me/+xxxx 或 https://t.me/频道名">
    <div class="fhint">填写后，机器人底部菜单会显示「📢 VIP频道」按钮；留空则隐藏该按钮</div>
  </div>
</div>

<div class="sc">
  <h2>⏰ 到期提醒设置</h2>

  <div class="frow">
    <label>到期前几天提醒</label>
    <input type="number" name="expiry_reminder_days" value="{expiry_reminder_days}"
      min="0" max="30" style="max-width:180px">
    <div class="fhint">会员到期前几天发送提醒消息（每天 0 点检查一次），设为 0 禁用</div>
  </div>

  <div class="frow">
    <label>到期提醒消息</label>
    <textarea name="expiry_reminder_message" rows="3">{expiry_reminder_message}</textarea>
    <div class="fhint">可用变量：{{days}}（剩余天数）、{{expiry_date}}（到期日期）</div>
  </div>
</div>

<div style="margin-top:8px; padding-bottom:32px;">
  <button class="btn-save" type="submit">保存配置</button>
</div>
</form>

</main>
</div>
""" + _THEME_SCRIPT + """
</body>
</html>"""


def _render_bot_config_page(cfg: dict, username: str, csrf_token: str, flash: str = "") -> str:
    return BOT_CONFIG_PAGE.format(
        sidebar=_nav_html("bot-config", username),
        flash=flash,
        csrf_token=_esc(csrf_token),
        bot_token="",
        admin_usernames=_esc(cfg.get("admin_usernames", "")),
        welcome_message=_esc(cfg.get("welcome_message", "")),
        vip_group_url=_esc(cfg.get("vip_group_url", "")),
        vip_channel_url=_esc(cfg.get("vip_channel_url", "")),
        order_timeout_minutes=_esc(cfg.get("order_timeout_minutes", "30")),
        expiry_reminder_days=_esc(cfg.get("expiry_reminder_days", "3")),
        expiry_reminder_message=_esc(cfg.get("expiry_reminder_message", "")),
    )


@admin_panel_router.get("/admin/bot-config", response_class=HTMLResponse)
async def bot_config_page(request: Request, msg: str = "", msg_type: str = "ok"):
    username = _get_session(request)
    if not username or not _password_changed():
        return RedirectResponse(url="/admin/login", status_code=303)

    from app.bot_config import get_bot_config

    flash = ""
    if msg:
        cls = "flash err" if msg_type == "err" else "flash"
        flash = f'<div class="{cls}">{_esc(msg)}</div>'

    csrf_token = request.cookies.get(SESSION_COOKIE, "")
    return HTMLResponse(content=_render_bot_config_page(get_bot_config(), username, csrf_token, flash))


@admin_panel_router.post("/admin/bot-config/save", response_class=HTMLResponse)
async def bot_config_save(request: Request):
    username = _get_session(request)
    if not username or not _password_changed():
        return RedirectResponse(url="/admin/login", status_code=303)

    form = await request.form()
    csrf_token = form.get("csrf_token", "")
    if not _csrf_check(request, csrf_token):
        return Response(status_code=401)

    from app.bot_config import get_bot_config, save_bot_config

    data = {k: v for k, v in form.items() if k != "csrf_token"}
    save_bot_config(data)

    flash = '<div class="flash">机器人配置已保存，即时生效</div>'
    csrf = request.cookies.get(SESSION_COOKIE, "")
    return HTMLResponse(content=_render_bot_config_page(get_bot_config(), username, csrf, flash))
