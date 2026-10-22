"""Phone dashboard (password protected).

Shows profit/loss per bot, win rate, best routes, balances, recent trades,
missed gaps and events. Has the KILL SWITCH and RESUME buttons and
spreadsheet downloads. Never shows secret keys.

Security:
  * password checked against a scrypt hash in the private settings file
  * signed, expiring session cookie (HttpOnly, SameSite=Strict)
  * every button carries an anti-forgery token
  * 5 wrong passwords -> locked out for 15 minutes
  * runs on 127.0.0.1 by default: reach it through Tailscale or HTTPS proxy
    (see docs/GOING_LIVE.md), never plain HTTP over the internet
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import secrets as pysecrets
import tempfile
import time
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from . import secrets_store
from .db import DB, today_utc
from .export import export_all
from .redact import scrub
from .routes import Route

SESSION_HOURS = 12
COOKIE = "xarb_session"
_fails: Dict[str, List[float]] = {}


def _sign(payload: bytes) -> str:
    mac = hmac.new(secrets_store.session_key(), payload, hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(payload).decode() + "." + mac


def _verify(token: Optional[str]) -> Optional[dict]:
    if not token or "." not in token:
        return None
    b64, mac = token.rsplit(".", 1)
    try:
        payload = base64.urlsafe_b64decode(b64.encode())
    except Exception:
        return None
    good = hmac.new(secrets_store.session_key(), payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(good, mac):
        return None
    data = json.loads(payload)
    if data.get("exp", 0) < time.time():
        return None
    return data


def _csrf(session: dict) -> str:
    return hmac.new(secrets_store.session_key(), ("csrf" + session["n"]).encode(), hashlib.sha256).hexdigest()


def _client_ip(req: Request) -> str:
    ip = req.client.host if req.client else "?"
    if ip in ("127.0.0.1", "::1"):
        ip = req.headers.get("x-forwarded-for", ip).split(",")[0].strip()
    return ip


def D(v) -> Decimal:
    return Decimal(v or "0")


CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#111;--muted:#666;--good:#0a7d32;--bad:#b3261e;--line:#e3e5e8;--btn:#1f6feb}
@media (prefers-color-scheme: dark){:root{--bg:#0f1115;--card:#181b21;--fg:#eee;--muted:#9aa0a6;--good:#3ddc84;--bad:#ff6b6b;--line:#2a2e35;--btn:#4c8dff}}
*{box-sizing:border-box}body{margin:0;font:15px/1.4 -apple-system,system-ui,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--fg)}
main{max-width:900px;margin:0 auto;padding:12px 16px 60px}h1{font-size:20px;margin:8px 0}h2{font-size:16px;margin:20px 0 8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;margin:8px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px}
.k{color:var(--muted);font-size:12px}.v{font-size:20px;font-weight:600}.good{color:var(--good)}.bad{color:var(--bad)}
table{width:100%;border-collapse:collapse;font-size:13px}td,th{padding:6px 4px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
.scroll{overflow-x:auto}button,.btn{display:inline-block;border:0;border-radius:10px;padding:12px 16px;font-size:15px;font-weight:600;color:#fff;background:var(--btn);text-decoration:none}
.kill{background:var(--bad);width:100%;font-size:18px;padding:16px}.pill{font-size:12px;padding:2px 8px;border-radius:99px;border:1px solid var(--line)}
input{font-size:16px;padding:10px;border-radius:8px;border:1px solid var(--line);width:100%;background:var(--card);color:var(--fg)}
.small{font-size:12px;color:var(--muted)}
"""


def page(title: str, body: str, refresh: bool = False) -> HTMLResponse:
    meta = '<meta http-equiv="refresh" content="20">' if refresh else ""
    return HTMLResponse(
        f"<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"{meta}<title>{html.escape(title)}</title><style>{CSS}</style></head><body><main>{body}</main></body></html>",
        headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer"},
    )


def create_app(db: Optional[DB] = None, network: str = "testnet") -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    db = db or DB()

    def session(req: Request) -> Optional[dict]:
        return _verify(req.cookies.get(COOKIE))

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/login")
    def login_form(err: str = ""):
        msg = f"<p class=bad>{html.escape(err)}</p>" if err else ""
        return page("Login", f"""<h1>XRPL bots</h1><div class=card><form method=post action=/login>
            {msg}<p><input type=password name=password placeholder=Password autofocus autocomplete=current-password></p>
            <button>Log in</button></form></div>""")

    @app.post("/login")
    def login(request: Request, password: str = Form(...)):
        ip = _client_ip(request)
        now = time.time()
        _fails[ip] = [t for t in _fails.get(ip, []) if now - t < 900]
        if len(_fails[ip]) >= 5:
            return page("Locked", "<h1>Too many attempts</h1><p>Try again in 15 minutes.</p>")
        if not secrets_store.verify_password(password, secrets_store.get("dashboard.password_hash")):
            _fails[ip].append(now)
            db.event(network, "dashboard", "WARN", "login", f"failed login from {ip}")
            return RedirectResponse("/login?err=Wrong+password", status_code=303)
        _fails.pop(ip, None)
        tok = _sign(json.dumps({"exp": now + SESSION_HOURS * 3600, "n": pysecrets.token_hex(8)}).encode())
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(COOKIE, tok, httponly=True, samesite="strict", secure=request.url.scheme == "https"
                        or request.headers.get("x-forwarded-proto") == "https", max_age=SESSION_HOURS * 3600)
        return resp

    @app.get("/logout")
    def logout():
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(COOKIE)
        return resp

    @app.get("/")
    def home(request: Request, net: str = network):
        s = session(request)
        if not s:
            return RedirectResponse("/login", status_code=303)
        return page("XRPL bots", render_home(db, net, _csrf(s)), refresh=True)

    def _check(request: Request, csrf: str) -> Optional[Response]:
        s = session(request)
        if not s:
            return RedirectResponse("/login", status_code=303)
        if not hmac.compare_digest(csrf, _csrf(s)):
            return Response("bad form token - reload the page", status_code=400)
        return None

    @app.post("/kill")
    def kill(request: Request, csrf: str = Form(...), confirm: str = Form("")):
        bad = _check(request, csrf)
        if bad:
            return bad
        if confirm.strip().upper() != "STOP":
            return page("Not confirmed", "<h1>Not stopped</h1><p>Type STOP in the box to confirm.</p><a class=btn href=/>Back</a>")
        db.set_flag("kill", "1")
        db.event(network, "dashboard", "WARN", "kill", f"kill switch pressed from {_client_ip(request)}")
        return page("Stopping", "<h1>Kill switch ON</h1><p>All bots are stopping and converting tokens to XRP. "
                    "You'll get an alert with the result.</p><a class=btn href=/>Back to dashboard</a>")

    @app.post("/resume")
    def resume(request: Request, csrf: str = Form(...)):
        bad = _check(request, csrf)
        if bad:
            return bad
        db.clear_flag("kill")
        db.set_flag("resume", "1")
        db.event(network, "dashboard", "INFO", "resume", f"resume pressed from {_client_ip(request)}")
        return RedirectResponse("/", status_code=303)

    @app.get("/export.xlsx")
    def export_xlsx(request: Request, net: str = network):
        if not session(request):
            return RedirectResponse("/login", status_code=303)
        d = Path(tempfile.mkdtemp(prefix="xarb-export-"))
        p = export_all(db, d, net)
        return FileResponse(p, filename=f"xrpl-bots-{net}-{today_utc()}.xlsx")

    return app


def _fmt(v: Decimal, dp: int = 4) -> str:
    cls = "good" if v > 0 else "bad" if v < 0 else ""
    return f"<span class={cls}>{v:+.{dp}f}</span>"


def render_home(db: DB, net: str, csrf: str) -> str:
    killed = db.get_flag("kill") == "1"
    hb = {r["bot"]: r for r in db.query("SELECT * FROM heartbeats")}
    bots = [r["bot"] for r in db.query("SELECT DISTINCT bot FROM balances WHERE network=? ORDER BY bot", (net,))]
    today = today_utc()
    out = [f"<h1>XRPL bots <span class=pill>{html.escape(net)}</span></h1>"]
    if killed:
        out.append("<div class='card bad'><b>KILL SWITCH IS ON.</b> No trading.</div>")

    tot_pnl = Decimal(0)
    tot_value = Decimal(0)
    cards = []
    for b in bots:
        first = db.query("SELECT total_value_xrp FROM balances WHERE network=? AND bot=? ORDER BY ts ASC LIMIT 1", (net, b))
        last = db.query("SELECT * FROM balances WHERE network=? AND bot=? ORDER BY ts DESC LIMIT 1", (net, b))
        start_v, start_ts = db.day_start_value(net, b, today)
        transfers_all = db.net_transfers_since(net, b, "0000")
        now_v = D(last[0]["total_value_xrp"]) if last else Decimal(0)
        pnl = now_v - D(first[0]["total_value_xrp"]) - transfers_all if first else Decimal(0)
        pnl_today = now_v - (start_v or now_v) - db.net_transfers_since(net, b, max(start_ts or today, today + "T00:00:00"))
        tr = db.query("SELECT status FROM trades WHERE network=? AND bot=? AND status IN ('win','loss','failed')", (net, b))
        wins = sum(1 for r in tr if r["status"] == "win")
        rate = f"{wins / len(tr) * 100:.0f}%" if tr else "–"
        h = hb.get(b)
        status = h["status"] if h else "unknown"
        detail = html.escape(scrub(h["detail"] or "")) if h else ""
        stale = h and (time.time() - _ts(h["ts"]) > 120)
        badge = "bad" if status != "running" or stale else "good"
        tot_pnl += pnl
        tot_value += now_v
        tokens_v = D(last[0]["tokens_value_xrp"]) if last else Decimal(0)
        cards.append(
            f"<div class=card><b>{b}</b> <span class='pill {badge}'>{'no heartbeat' if stale else status}</span>"
            f"<div class=small>{detail}</div><div class=grid>"
            f"<div><div class=k>Value now</div><div class=v>{now_v:.3f}</div><div class=small>tokens: {tokens_v:.4f} XRP</div></div>"
            f"<div><div class=k>P&L today</div><div class=v>{_fmt(pnl_today)}</div></div>"
            f"<div><div class=k>P&L total</div><div class=v>{_fmt(pnl)}</div></div>"
            f"<div><div class=k>Win rate</div><div class=v>{rate}</div><div class=small>{len(tr)} attempts</div></div>"
            f"</div></div>"
        )
    out.append(
        f"<div class=card><div class=grid><div><div class=k>Total value (XRP)</div><div class=v>{tot_value:.3f}</div></div>"
        f"<div><div class=k>Total P&L (XRP)</div><div class=v>{_fmt(tot_pnl)}</div></div></div>"
        f"<div class=small>Values count tokens at what they'd sell for right now, not face value.</div></div>"
    )
    out += cards or ["<div class=card>No bot data yet.</div>"]

    best = db.query(
        "SELECT route, COUNT(*) n, SUM(CAST(profit_xrp AS REAL)) p FROM trades WHERE network=? AND status IN ('win','loss','failed') "
        "GROUP BY route ORDER BY p DESC LIMIT 8", (net,))
    out.append("<h2>Best routes</h2><div class='card scroll'><table><tr><th>Route</th><th>Trades</th><th>P&L</th></tr>")
    for r in best:
        out.append(f"<tr><td>{html.escape(_label(r['route']))}</td><td>{r['n']}</td><td>{_fmt(Decimal(str(r['p'] or 0)))}</td></tr>")
    out.append("</table></div>")

    recent = db.query("SELECT * FROM trades WHERE network=? ORDER BY ts DESC LIMIT 15", (net,))
    out.append("<h2>Recent trades</h2><div class='card scroll'><table><tr><th>When (UTC)</th><th>Bot</th><th>Route</th><th>Result</th><th>P&L</th></tr>")
    for r in recent:
        out.append(f"<tr><td>{r['ts'][5:19].replace('T', ' ')}</td><td>{r['bot']}</td><td>{html.escape(_label(r['route']))}</td>"
                   f"<td>{html.escape(r['status'] or '')}</td><td>{_fmt(D(r['profit_xrp']), 5)}</td></tr>")
    out.append("</table></div>")

    missed = db.query(
        "SELECT reason, COUNT(*) n, MAX(CAST(net_profit_xrp AS REAL)) best FROM opportunities "
        "WHERE network=? AND action='missed' AND ts>=? GROUP BY reason ORDER BY n DESC LIMIT 8", (net, today))
    out.append("<h2>Gaps missed today (and why)</h2><div class='card scroll'><table><tr><th>Reason</th><th>Count</th><th>Biggest</th></tr>")
    for r in missed:
        out.append(f"<tr><td>{html.escape(r['reason'])}</td><td>{r['n']}</td><td>{(r['best'] or 0):.5f}</td></tr>")
    out.append("</table></div>")

    ev = db.query("SELECT * FROM events WHERE level IN ('WARN','ERROR','ALERT') ORDER BY ts DESC LIMIT 10")
    out.append("<h2>Alerts & events</h2><div class='card scroll'><table>")
    for r in ev:
        out.append(f"<tr><td class=small>{r['ts'][5:19].replace('T', ' ')}</td><td>{html.escape(scrub(r['message'] or ''))[:300]}</td></tr>")
    out.append("</table></div>")

    w = db.query(
        "SELECT COUNT(*) n, SUM(CASE WHEN scans>=2 AND scans<=75 AND all_tokens_pass=1 THEN 1 ELSE 0 END) c, "
        "MIN(first_seen) f FROM watch_episodes WHERE network='mainnet-watch'")
    if w and w[0]["n"]:
        out.append(
            "<h2>Mainnet watch-only</h2><div class=card>"
            f"Since {html.escape((w[0]['f'] or '')[:10])}: {w[0]['n']} profitable gaps seen, "
            f"{w[0]['c'] or 0} realistically catchable with safe tokens. "
            "<div class=small>Full report: python -m xrplarb watch-report</div></div>"
        )
    out.append(f"<h2>Spreadsheet</h2><a class=btn href='/export.xlsx?net={html.escape(net)}'>Download Excel (tax + logs)</a>")
    out.append(
        "<h2>Controls</h2><div class=card><form method=post action=/kill>"
        f"<input type=hidden name=csrf value={csrf}><p>Stops ALL bots and sells every token back to XRP.</p>"
        "<p><input name=confirm placeholder='Type STOP to confirm' autocomplete=off></p>"
        "<button class=kill>KILL SWITCH</button></form>"
        f"<form method=post action=/resume style='margin-top:12px'><input type=hidden name=csrf value={csrf}>"
        "<button>Resume all bots</button></form>"
        "<p class=small>Resume also clears loss-cap stops and crash stops.</p></div>"
        "<p><a href=/logout>Log out</a></p>"
    )
    return "".join(out)


def _label(route_key: str) -> str:
    if ">" not in route_key:
        return route_key
    try:
        return Route.from_key(route_key).label
    except Exception:
        return route_key


def _ts(iso: str) -> float:
    from datetime import datetime, timezone

    return datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
