"""The "Live" page of the dashboard: an animated portal view of the bots.

Same login as the dashboard, served from it at /live. The page asks
/live.json for fresh numbers every few seconds; everything is drawn by the
page itself (no outside images or fonts, so no other site sees you looking).
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Dict, List

from .config import BOT_NICKNAMES
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
            "nick": BOT_NICKNAMES.get(b, b),
            "status": (h["status"] if alive else "no heartbeat") if h else "unknown",
            "trades": t["n"] or 0,
            "profit": round(t["p"] or 0.0, 4),
            "last_age": round(_age(t["last"])) if t["last"] else None,
        })

    feed = []  # testnet trades
    for t in db.query(
        "SELECT id, ts, bot, route, status, profit_xrp FROM trades WHERE network=? AND status IN ('win','loss','failed') "
        "ORDER BY ts DESC LIMIT 25", (network,)):
        feed.append({"id": f"t{t['id']}", "kind": "testnet", "age": round(_age(t["ts"])), "when": fmt_local(t["ts"]),
                     "bot": t["bot"], "nick": BOT_NICKNAMES.get(t["bot"], t["bot"]),
                     "tokens": _symbols(t["route"]), "status": t["status"],
                     "profit": round(_num(t["profit_xrp"]), 4)})

    paper = {"shown": False}
    paper_feed: List[Dict] = []
    paper_tokens: List[Dict] = []
    last = db.query("SELECT ts, live FROM watch_scans WHERE network='mainnet-watch' ORDER BY ts DESC LIMIT 1")
    if last:
        d = paper_day(db, "mainnet", today)
        # the tokens with the most would-be trades today orbit the portal in paper mode
        per: Dict[str, Dict] = {}
        for e in d["traded"]:
            for sym in set(_symbols(e["route"])[1:-1]):
                t = per.setdefault(sym, {"name": sym, "nick": sym, "trades": 0, "profit": 0.0, "last_age": None, "status": "running"})
                t["trades"] += 1
                t["profit"] = round(t["profit"] + float(episode_profit(e)), 4)
                age = round(_age(e["first_seen"]))
                t["last_age"] = age if t["last_age"] is None else min(t["last_age"], age)
        paper_tokens = sorted(per.values(), key=lambda t: (-t["trades"], -t["profit"]))[:5]
        paper = {
            "shown": True,
            "scanning": _age(last[0]["ts"]) < 120,
            "live": bool(last[0]["live"]),
            "speed": round((d["scans"]["ld"] or 0) / 1000, 2) if d["scans"]["l"] else None,
            "trades": d["bursts"],
            "profit": round(float(d["bursts_profit"]), 4),
            "missed": d["bursts_missed"],
        }
        # would-be trades (gap still open a ledger later) and gaps missed on speed
        for e in db.query(
            "SELECT id, first_seen, route, first_safe_profit_xrp, max_safe_profit_xrp, scans FROM watch_episodes "
            "WHERE network='mainnet-watch' AND would_trade=1 AND scans<=? ORDER BY first_seen DESC LIMIT 30",
            (SUSPICIOUS_SCANS,)):
            toks = _symbols(e["route"])
            paper_feed.append({"id": f"p{e['id']}", "kind": "paper", "age": round(_age(e["first_seen"])),
                               "when": fmt_local(e["first_seen"]), "bot": toks[1] if len(toks) > 2 else "paper",
                               "tokens": toks, "status": "win" if e["scans"] >= 2 else "missed",
                               "profit": round(float(episode_profit(e)), 4)})
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
        "paper_tokens": paper_tokens,
        "feed": feed[:30],
        "paper_feed": paper_feed,
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
.stage{position:relative;height:400px;margin:4px 0 0}
.portal{position:absolute;left:50%;top:50%;width:168px;height:168px;transform:translate(-50%,-50%);border-radius:50%;
filter:drop-shadow(0 0 22px #7dff3aaa) drop-shadow(0 0 50px #1fe0c455)}
.ring{position:absolute;inset:0;border-radius:50%;
background:conic-gradient(from 0deg,#7dff3a,#1fe0c4,#c6ff6b,#0d6b3a,#7dff3a,#2bffb0,#7dff3a);
-webkit-mask:radial-gradient(circle,transparent 30%,#000 31%,#000 70%,transparent 71%);
mask:radial-gradient(circle,transparent 30%,#000 31%,#000 70%,transparent 71%);animation:spin 6s linear infinite}
.ring.r2{inset:14px;opacity:.75;animation-duration:3.8s;animation-direction:reverse;filter:blur(1px)}
.ring.r3{inset:30px;opacity:.6;animation-duration:2.4s;filter:blur(2px)}
.core{position:absolute;inset:52px;border-radius:50%;background:radial-gradient(circle,#eaffd6 0,#9dff4f 35%,#1b7a3c 70%,#062614 100%);
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
.switch{display:flex;border:1px solid var(--line);border-radius:99px;padding:3px;background:#08120dcc;margin:10px 0 0}
.switch button{flex:1;border:0;background:none;color:var(--muted);font:inherit;font-size:12px;letter-spacing:.14em;padding:8px 6px;border-radius:99px;cursor:pointer}
.switch button.on{background:linear-gradient(90deg,#1b5e33,#0e6b5e);color:var(--fg);box-shadow:0 0 12px #7dff3a55}
.pod.tokpod .ship{display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:700;color:#04110a;border-radius:50%}
a{color:var(--portal2)}footer{margin-top:18px;font-size:11px;color:var(--muted);display:flex;justify-content:space-between;gap:8px}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
</style></head><body><main>
<header><div><h1>PORTAL OPS</h1><div class=sub>Interdimensional arbitrage lab</div></div>
<div class=pill><span class=dot id=dot></span><span id=clock>connecting…</span></div></header>
<div class=switch role=tablist><button id=mT data-m=testnet>T-NET · TESTNET</button><button id=mP data-m=paper>M-NET · PAPER</button></div>
<div class=banner id=banner></div>
<div class=stage id=stage><div class=portal id=portal><div class=ring></div><div class="ring r2"></div><div class="ring r3"></div>
<div class=swirl></div><div class=core></div></div></div>
<div class=legend id=legend></div>
<div class=stats>
<div class=stat><div class=k id=k1></div><div class=v id=v1>–</div><div class=s id=s1>&nbsp;</div></div>
<div class=stat><div class=k id=k2></div><div class=v id=v2>–</div><div class=s id=s2>&nbsp;</div></div>
<div class=stat><div class=k id=k3></div><div class=v id=v3>–</div><div class=s id=s3>&nbsp;</div></div>
<div class=stat><div class=k id=k4></div><div class=v id=v4>–</div><div class=s id=s4>&nbsp;</div></div>
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
const pods={};let seen=new Set(),first=true,lastOk=0,last=null;
let mode="testnet";try{mode=localStorage.getItem("portal-mode")||"testnet"}catch(e){}
function setMode(m){mode=m;try{localStorage.setItem("portal-mode",m)}catch(e){}for(const k in pods){pods[k].remove();delete pods[k]}
$("mT").classList.toggle("on",m==="testnet");$("mP").classList.toggle("on",m==="paper");first=true;seen=new Set();if(last)render(last)}
document.querySelectorAll(".switch button").forEach(b=>b.addEventListener("click",()=>setMode(b.dataset.m)));
function card(i,k,v,s,cls){$("k"+i).textContent=k;$("v"+i).textContent=v;$("v"+i).className="v "+(cls||"");$("s"+i).textContent=s}
function placePods(bots){const st=$("stage"),W=st.clientWidth,H=st.clientHeight,cx=W/2,cy=H/2,R=Math.min(W*0.42,150),Ry=Math.min(H/2-62,140);
bots.forEach((b,i)=>{let p=pods[b.name];if(!p){p=document.createElement("div");p.className="pod"+(mode==="paper"?" tokpod":"");p.innerHTML='<div class=ship></div><div class=nm></div><div class=pf></div>';
st.append(p);pods[b.name]=p}const a=-Math.PI/2+i*2*Math.PI/bots.length;p.style.left=(cx+R*Math.cos(a))+"px";p.style.top=(cy+Ry*Math.sin(a))+"px";
p.querySelector(".nm").textContent=(b.nick||b.name).toUpperCase();p.title=b.name;
if(mode==="paper"){const sh=p.querySelector(".ship");sh.textContent=[...b.name].slice(0,3).join("");sh.style.background=`hsl(${hue(b.name)} 85% 60%)`}p.querySelector(".pf").textContent=`${fmt(b.profit,2)} · ${b.trades}`;
p.classList.toggle("hot",b.last_age!==null&&b.last_age<(mode==="paper"?600:90)&&b.status==="running");p.classList.toggle("halt",b.status!=="running")})}
function zap(item){const st=$("stage"),pod=pods[item.bot],por=$("portal");const z=document.createElement("div");z.className="zap";
item.tokens.slice(1,-1).forEach(t=>z.append(tok(t)));st.append(z);const sx=pod?parseFloat(pod.style.left):st.clientWidth/2,sy=pod?parseFloat(pod.style.top):20;
z.style.transform=`translate(${sx-20}px,${sy-13}px)`;if(pod){pod.classList.remove("jump");void pod.offsetWidth;pod.classList.add("jump")}
requestAnimationFrame(()=>requestAnimationFrame(()=>{z.style.transform=`translate(${st.clientWidth/2-20}px,${st.clientHeight/2-13}px) scale(.3)`;z.style.opacity="0"}));
setTimeout(()=>{z.remove();if(item.status==="win"){por.classList.remove("burst");void por.offsetWidth;por.classList.add("burst")}},900)}
function render(d){lastOk=Date.now();$("dot").classList.remove("off");$("clock").textContent="LIVE · "+new Date().toLocaleTimeString([], {hour:"numeric",minute:"2-digit"});
$("tz").textContent=d.tz;const b=$("banner");b.style.display=d.killed?"block":"none";b.textContent=d.killed?"KILL SWITCH IS ON - portal closed, no trading.":"";
last=d;const t=d.testnet,p=d.paper,paper=mode==="paper";
if(!paper){const up=d.bots.filter(b=>b.status==="running").length;
$("legend").innerHTML='<span><b class=good>●</b> jumped (win)</span><span><b class=bad>●</b> fizzled (no-fill)</span><span><span class="tag testnet">T-NET</span> testnet bots, test XRP</span>';
card(1,"Profit today",fmt(t.profit,3),"test XRP","good");card(2,"Jumps today",t.trades,t.trades?`${t.wins} jumped · ${t.no_fills} fizzled`:"no jumps yet today");
card(3,"Win rate",t.win_rate===null?"–":t.win_rate+"%",`${t.losses} losses`,"good");card(4,"Crew online",`${up}/${d.bots.length}`,up===d.bots.length?"all bots running":"check the dashboard",up===d.bots.length?"good":"bad")}
else{$("legend").innerHTML='<span><b class=good>●</b> would-be trade</span><span><b class=bad>●</b> missed on speed</span><span><span class="tag paper">M-NET</span> mainnet paper, no XRP spent</span>';
if(p.shown){card(1,"Paper profit today",fmt(p.profit,3),"after all fees","good");card(2,"Would-be trades",p.trades,"approved tokens only");
card(3,"Missed on speed",p.missed,"gone before we could act",p.missed?"bad":"");
card(4,"Portal lag",p.speed!==null?p.speed.toFixed(2)+"s":"–",!p.scanning?"paper trading stopped":p.live?"after each ledger":"slow reads (reconnecting)",p.scanning&&p.live?"good":"bad")}
else{card(1,"Paper trading","off","not running on this server","bad");for(const i of[2,3,4])card(i,"","","")}}
const orbit=paper?(d.paper_tokens.length?d.paper_tokens:[{name:"SCANNER",nick:"SCANNER",trades:p.trades||0,profit:p.profit||0,last_age:null,status:p.scanning?"running":"stopped"}]):d.bots;
for(const k in pods)if(!orbit.some(b=>b.name===k)){pods[k].remove();delete pods[k]}
placePods(orbit);const feed=$("feed");feed.innerHTML="";const fresh=[];const items=paper?d.paper_feed:d.feed;
items.forEach(it=>{const r=document.createElement("div");r.className="row";if(!seen.has(it.id)&&!first){r.classList.add("new");fresh.push(it)}
r.append(route(it.tokens));const m=document.createElement("div");m.className="meta";
m.innerHTML=`<div class="p ${it.status==="win"?"good":"bad"}">${it.status==="win"?fmt(it.profit):it.status==="missed"?"missed "+fmt(it.profit):"fizzled"}</div><div class=w><span class="tag ${it.kind}">${it.kind==="paper"?"M-NET":"T-NET"}</span> ${it.kind==="paper"?"paper":(it.nick||it.bot)} · ${ago(it.age)}</div>`;
r.append(m);feed.append(r)});if(!items.length)feed.innerHTML='<div class=row><span class=w>No portal traffic yet.</span></div>';
fresh.slice(0,6).reverse().forEach((it,i)=>setTimeout(()=>zap(it),i*350));seen=new Set(items.map(x=>x.id));first=false}
async function tick(){try{const r=await fetch("/live.json",{cache:"no-store",credentials:"same-origin"});if(r.status===401||r.redirected){location.href="/login";return}
render(await r.json())}catch(e){}if(Date.now()-lastOk>15000){$("dot").classList.add("off");$("clock").textContent="signal lost…"}}
setMode(mode);tick();setInterval(tick,4000);addEventListener("resize",()=>{for(const k in pods)pods[k].remove();for(const k in pods)delete pods[k]});
</script></body></html>"""
