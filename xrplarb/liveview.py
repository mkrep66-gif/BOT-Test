"""The "Live" page of the dashboard: an animated portal view of the bots.

Same login as the dashboard, served from it at /live. The page asks
/live.json for fresh numbers every few seconds; everything is drawn by the
page itself (no outside images or fonts, so no other site sees you looking).
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Dict, List

from .db import DB
from .history import day_result
from .localtime import day_start_utc, fmt_local, today_local, tz_label
from .routes import Route


def _age(iso: str) -> float:
    from datetime import datetime, timezone

    return time.time() - datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()


def _symbols(route_key: str) -> List[str]:
    try:
        return [a.symbol for a in Route.from_key(route_key).assets]
    except Exception:
        return []


def _num(v) -> float:
    try:
        return float(Decimal(str(v or 0)))
    except Exception:
        return 0.0


def live_data(db: DB, network: str = "testnet") -> Dict:
    from .watch import SUSPICIOUS_SCANS, episode_profit, paper_day

    today = today_local()
    midnight = day_start_utc(today)
    r = day_result(db, network, today)
    hb = {h["bot"]: h for h in db.query("SELECT * FROM heartbeats")}
    bots = []
    for b in sorted({row["bot"] for row in db.query("SELECT DISTINCT bot FROM balances WHERE network=?", (network,))}):
        t = db.query(
            "SELECT COUNT(*) n, SUM(CAST(profit_xrp AS REAL)) p, MAX(ts) last FROM trades "
            "WHERE network=? AND bot=? AND ts>=? AND status IN ('win','loss','failed')", (network, b, midnight))[0]
        h = hb.get(b)
        alive = bool(h) and _age(h["ts"]) < 120
        bots.append({
            "name": b,
            "status": (h["status"] if alive else "no heartbeat") if h else "unknown",
            "trades": t["n"] or 0,
            "profit": round(t["p"] or 0.0, 4),
            "last_age": round(_age(t["last"])) if t["last"] else None,
        })

    feed = []
    for t in db.query(
        "SELECT id, ts, bot, route, status, profit_xrp FROM trades WHERE network=? AND status IN ('win','loss','failed') "
        "ORDER BY ts DESC LIMIT 25", (network,)):
        feed.append({"id": f"t{t['id']}", "kind": "testnet", "age": round(_age(t["ts"])), "when": fmt_local(t["ts"]),
                     "bot": t["bot"], "tokens": _symbols(t["route"]), "status": t["status"],
                     "profit": round(_num(t["profit_xrp"]), 4)})

    paper = {"shown": False}
    last = db.query("SELECT ts, live FROM watch_scans WHERE network='mainnet-watch' ORDER BY ts DESC LIMIT 1")
    if last:
        d = paper_day(db, "mainnet", today)
        paper = {
            "shown": True,
            "scanning": _age(last[0]["ts"]) < 120,
            "live": bool(last[0]["live"]),
            "speed": round((d["scans"]["ld"] or 0) / 1000, 2) if d["scans"]["l"] else None,
            "trades": len(d["traded"]),
            "profit": round(float(d["profit"]), 4),
            "missed": len(d["too_fast"]),
        }
        for e in db.query(
            "SELECT id, first_seen, route, first_safe_profit_xrp, max_safe_profit_xrp, scans FROM watch_episodes "
            "WHERE network='mainnet-watch' AND would_trade=1 AND scans>=2 AND scans<=? ORDER BY first_seen DESC LIMIT 15",
            (SUSPICIOUS_SCANS,)):
            feed.append({"id": f"p{e['id']}", "kind": "paper", "age": round(_age(e["first_seen"])),
                         "when": fmt_local(e["first_seen"]), "bot": "paper", "tokens": _symbols(e["route"]),
                         "status": "win", "profit": round(float(episode_profit(e)), 4)})
    feed.sort(key=lambda x: x["age"])
    attempts = r.attempts or 0
    return {
        "network": network,
        "tz": tz_label(),
        "testnet": {
            "profit": round(float(r.trade_profit), 4),
            "trades": attempts,
            "wins": r.wins,
            "no_fills": r.no_fills,
            "losses": r.losses,
            "win_rate": round(r.wins / attempts * 100) if attempts else None,
        },
        "killed": db.get_flag("kill") == "1",
        "bots": bots,
        "paper": paper,
        "feed": feed[:30],
    }


LIVE_HTML = r"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><meta name=color-scheme content="dark">
<title>Portal Ops</title><style>
:root{--bg:#05080a;--panel:#0b1410;--line:#16352a;--fg:#e9fff4;--muted:#7fa596;--portal:#7dff3a;--portal2:#1fe0c4;
--slime:#b6ff3b;--bad:#ff5d7a;--warn:#ffd23b}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--fg)}
body{font:14px/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;overflow-x:hidden;
background:radial-gradient(ellipse at 50% 18%,#0d2a1c 0,#05080a 60%) fixed}
main{max-width:720px;margin:0 auto;padding:12px 16px 40px}
header{display:flex;justify-content:space-between;align-items:flex-start;gap:8px}
h1{margin:0;font-size:22px;letter-spacing:.18em;color:var(--slime);text-shadow:0 0 12px #7dff3a88}
.sub{color:var(--muted);font-size:11px;letter-spacing:.12em;text-transform:uppercase}
.pill{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);border-radius:99px;padding:4px 10px;
font-size:11px;letter-spacing:.1em;white-space:nowrap;background:#08120dcc}
.dot{width:8px;height:8px;border-radius:50%;background:var(--portal);box-shadow:0 0 8px var(--portal);animation:blink 1.6s infinite}
.dot.off{background:var(--bad);box-shadow:0 0 8px var(--bad)}
@keyframes blink{50%{opacity:.35}}
.stage{position:relative;height:340px;margin:8px 0 4px}
.portal{position:absolute;left:50%;top:50%;width:190px;height:190px;transform:translate(-50%,-50%);border-radius:50%;
filter:drop-shadow(0 0 22px #7dff3aaa) drop-shadow(0 0 50px #1fe0c455)}
.ring{position:absolute;inset:0;border-radius:50%;
background:conic-gradient(from 0deg,#7dff3a,#1fe0c4,#c6ff6b,#0d6b3a,#7dff3a,#2bffb0,#7dff3a);
-webkit-mask:radial-gradient(circle,transparent 30%,#000 31%,#000 70%,transparent 71%);
mask:radial-gradient(circle,transparent 30%,#000 31%,#000 70%,transparent 71%);animation:spin 6s linear infinite}
.ring.r2{inset:14px;opacity:.75;animation-duration:3.8s;animation-direction:reverse;filter:blur(1px)}
.ring.r3{inset:34px;opacity:.6;animation-duration:2.4s;filter:blur(2px)}
.core{position:absolute;inset:58px;border-radius:50%;background:radial-gradient(circle,#eaffd6 0,#9dff4f 35%,#1b7a3c 70%,#062614 100%);
animation:pulse 2.2s ease-in-out infinite}
.swirl{position:absolute;inset:0;border-radius:50%;background:repeating-conic-gradient(from 0deg,#ffffff14 0 8deg,transparent 8deg 22deg);
animation:spin 9s linear infinite reverse}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes pulse{50%{transform:scale(1.08);filter:brightness(1.25)}}
.portal.burst .core{animation:burst .7s ease-out}
@keyframes burst{0%{transform:scale(1)}40%{transform:scale(1.35);filter:brightness(2)}100%{transform:scale(1)}}
.pod{position:absolute;width:74px;margin:-30px 0 0 -37px;text-align:center;transition:transform .3s}
.pod .ship{width:38px;height:38px;margin:0 auto;border-radius:50% 50% 45% 45%;border:2px solid var(--line);
background:radial-gradient(circle at 50% 35%,#cfe 0,#4a6 30%,#123 75%);box-shadow:0 0 0 #0000;position:relative}
.pod .ship:after{content:"";position:absolute;left:50%;bottom:-7px;width:16px;height:7px;margin-left:-8px;border-radius:50%;
background:#7dff3a55;filter:blur(2px)}
.pod .nm{font-size:11px;letter-spacing:.1em;margin-top:6px}.pod .pf{font-size:11px;color:var(--muted)}
.pod.hot .ship{border-color:var(--portal);box-shadow:0 0 16px var(--portal)}
.pod.halt .ship{border-color:var(--bad);box-shadow:0 0 14px var(--bad);filter:grayscale(.6)}
.pod.jump{animation:jump .8s ease-out}
@keyframes jump{30%{transform:scale(1.25)}100%{transform:scale(1)}}
.zap{position:absolute;left:0;top:0;pointer-events:none;display:flex;gap:2px;transition:transform .9s cubic-bezier(.5,0,.8,.4),opacity .9s}
.tok{display:inline-flex;align-items:center;justify-content:center;width:26px;height:26px;border-radius:50%;font-size:9px;font-weight:700;
color:#04110a;border:1px solid #ffffff55;flex:none;overflow:hidden;letter-spacing:0}
.stats{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin:8px 0}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:10px 12px}
.stat .k{font-size:10px;letter-spacing:.14em;color:var(--muted);text-transform:uppercase}
.stat .v{font-size:24px;font-weight:700;margin-top:2px}.stat .s{font-size:11px;color:var(--muted)}
.good{color:var(--slime);text-shadow:0 0 10px #7dff3a55}.bad{color:var(--bad)}
h2{font-size:12px;letter-spacing:.2em;color:var(--muted);text-transform:uppercase;margin:16px 0 6px}
.feed{display:flex;flex-direction:column;gap:6px}
.row{display:flex;align-items:center;gap:8px;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:8px 10px;
min-width:0}
.row.new{animation:slidein .6s ease-out}
@keyframes slidein{from{transform:translateY(-10px);opacity:0;box-shadow:0 0 18px var(--portal)}to{transform:none;opacity:1}}
.row .route{display:flex;align-items:center;gap:2px;flex:1;min-width:0;overflow:hidden}
.row .route .ar{color:var(--muted);font-size:10px}
.row .meta{text-align:right;flex:none}
.row .p{font-weight:700}.row .w{font-size:10px;color:var(--muted);letter-spacing:.06em}
.tag{font-size:9px;letter-spacing:.12em;border-radius:6px;padding:1px 5px;border:1px solid var(--line);color:var(--muted)}
.tag.paper{color:var(--portal2);border-color:#1fe0c455}.tag.testnet{color:var(--slime);border-color:#7dff3a44}
.legend{display:flex;gap:12px;font-size:11px;color:var(--muted);flex-wrap:wrap}
.banner{border:1px solid var(--bad);color:var(--bad);border-radius:12px;padding:8px 12px;margin:8px 0;display:none}
a{color:var(--portal2)}footer{margin-top:18px;font-size:11px;color:var(--muted);display:flex;justify-content:space-between;gap:8px}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
</style></head><body><main>
<header><div><h1>PORTAL OPS</h1><div class=sub>Interdimensional arbitrage lab</div></div>
<div class=pill><span class=dot id=dot></span><span id=clock>connecting…</span></div></header>
<div class=banner id=banner></div>
<div class=stage id=stage><div class=portal id=portal><div class=ring></div><div class="ring r2"></div><div class="ring r3"></div>
<div class=swirl></div><div class=core></div></div></div>
<div class=legend><span><b class=good>●</b> jumped (win)</span><span><b class=bad>●</b> fizzled (no-fill)</span>
<span><span class="tag testnet">T-NET</span> testnet bots</span><span><span class="tag paper">M-NET</span> mainnet paper</span></div>
<div class=stats>
<div class=stat><div class=k>Profit today · T-net</div><div class="v good" id=tp>–</div><div class=s id=tps>test XRP</div></div>
<div class=stat><div class=k>Jumps today · T-net</div><div class=v id=tt>–</div><div class=s id=tts>&nbsp;</div></div>
<div class=stat><div class=k>Paper profit · M-net</div><div class="v good" id=pp>–</div><div class=s id=pps>&nbsp;</div></div>
<div class=stat><div class=k>Portal lag · M-net</div><div class=v id=pl>–</div><div class=s id=pls>after each ledger</div></div>
</div>
<h2>Portal traffic</h2><div class=feed id=feed></div>
<footer><a href="/">← Lab dashboard</a><span id=tz></span></footer>
</main><script>
const $=id=>document.getElementById(id);
const fmt=(v,dp=4)=>(v>0?"+":"")+Number(v).toFixed(dp);
function hue(s){let h=0;for(const c of s)h=(h*31+c.codePointAt(0))>>>0;return h%360}
function tok(sym){const s=document.createElement("span");s.className="tok";const label=sym==="XRP"?"XRP":[...sym].slice(0,3).join("");
s.textContent=label;s.title=sym;s.style.background=sym==="XRP"?"#e9fff4":`hsl(${hue(sym)} 85% 60%)`;return s}
function route(tokens){const d=document.createElement("div");d.className="route";tokens.forEach((t,i)=>{if(i){const a=document.createElement("span");
a.className="ar";a.textContent="›";d.append(a)}d.append(tok(t))});return d}
function ago(s){if(s<60)return s+"s ago";if(s<3600)return Math.round(s/60)+"m ago";return Math.round(s/3600)+"h ago"}
const pods={};let seen=new Set(),first=true,lastOk=0;
function placePods(bots){const st=$("stage"),W=st.clientWidth,H=st.clientHeight,cx=W/2,cy=H/2,R=Math.min(W*0.42,150),Ry=Math.min(H*0.42,140);
bots.forEach((b,i)=>{let p=pods[b.name];if(!p){p=document.createElement("div");p.className="pod";p.innerHTML='<div class=ship></div><div class=nm></div><div class=pf></div>';
st.append(p);pods[b.name]=p}const a=-Math.PI/2+i*2*Math.PI/bots.length;p.style.left=(cx+R*Math.cos(a))+"px";p.style.top=(cy+Ry*Math.sin(a))+"px";
p.querySelector(".nm").textContent=b.name.toUpperCase();p.querySelector(".pf").textContent=`${fmt(b.profit,2)} · ${b.trades}`;
p.classList.toggle("hot",b.last_age!==null&&b.last_age<90&&b.status==="running");p.classList.toggle("halt",b.status!=="running")})}
function zap(item){const st=$("stage"),pod=pods[item.bot],por=$("portal");const z=document.createElement("div");z.className="zap";
item.tokens.slice(1,-1).forEach(t=>z.append(tok(t)));st.append(z);const sx=pod?parseFloat(pod.style.left):st.clientWidth/2,sy=pod?parseFloat(pod.style.top):20;
z.style.transform=`translate(${sx-20}px,${sy-13}px)`;if(pod){pod.classList.remove("jump");void pod.offsetWidth;pod.classList.add("jump")}
requestAnimationFrame(()=>requestAnimationFrame(()=>{z.style.transform=`translate(${st.clientWidth/2-20}px,${st.clientHeight/2-13}px) scale(.3)`;z.style.opacity="0"}));
setTimeout(()=>{z.remove();if(item.status==="win"){por.classList.remove("burst");void por.offsetWidth;por.classList.add("burst")}},900)}
function render(d){lastOk=Date.now();$("dot").classList.remove("off");$("clock").textContent="LIVE · "+new Date().toLocaleTimeString([], {hour:"numeric",minute:"2-digit"});
$("tz").textContent=d.tz;const b=$("banner");b.style.display=d.killed?"block":"none";b.textContent=d.killed?"KILL SWITCH IS ON - portal closed, no trading.":"";
const t=d.testnet;$("tp").textContent=fmt(t.profit,3);$("tt").textContent=t.trades;
$("tts").textContent=t.trades?`${t.wins} jumped · ${t.no_fills} fizzled · ${t.win_rate}% win`:"no jumps yet today";
const p=d.paper;if(p.shown){$("pp").textContent=fmt(p.profit,3);$("pps").textContent=`${p.trades} would-be · ${p.missed} missed`;
$("pl").textContent=p.speed!==null?p.speed.toFixed(2)+"s":"–";$("pls").textContent=!p.scanning?"paper trading stopped":p.live?"live feed · after each ledger":"slow reads (reconnecting)";
$("pl").className="v "+(p.scanning&&p.live?"good":"bad")}else{$("pp").textContent="–";$("pps").textContent="not running";$("pl").textContent="–"}
placePods(d.bots);const feed=$("feed");feed.innerHTML="";const fresh=[];
d.feed.forEach(it=>{const r=document.createElement("div");r.className="row";if(!seen.has(it.id)&&!first){r.classList.add("new");fresh.push(it)}
r.append(route(it.tokens));const m=document.createElement("div");m.className="meta";
m.innerHTML=`<div class="p ${it.status==="win"?"good":"bad"}">${it.status==="win"?fmt(it.profit):"fizzled"}</div><div class=w><span class="tag ${it.kind}">${it.kind==="paper"?"M-NET":"T-NET"}</span> ${it.kind==="paper"?"paper":it.bot} · ${ago(it.age)}</div>`;
r.append(m);feed.append(r)});if(!d.feed.length)feed.innerHTML='<div class=row><span class=w>No portal traffic yet today.</span></div>';
fresh.slice(0,6).reverse().forEach((it,i)=>setTimeout(()=>zap(it),i*350));seen=new Set(d.feed.map(x=>x.id));first=false}
async function tick(){try{const r=await fetch("/live.json",{cache:"no-store",credentials:"same-origin"});if(r.status===401||r.redirected){location.href="/login";return}
render(await r.json())}catch(e){}if(Date.now()-lastOk>15000){$("dot").classList.add("off");$("clock").textContent="signal lost…"}}
tick();setInterval(tick,4000);addEventListener("resize",()=>{for(const k in pods)pods[k].remove();for(const k in pods)delete pods[k]});
</script></body></html>"""
