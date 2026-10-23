"""Watch-only mode: scan real mainnet markets, never trade, need no keys.

Every scan, every loop is priced exactly as a bot would price it (same fees,
same slippage buffer, same size limits). Each continuous run of a profitable
gap on a route is recorded as one "episode", so a gap that lasts 10 scans
counts once, not ten times.

The report answers: how many real opportunities were there, how big, how
long did they last, and would they have been profitable after all costs?
"""

from __future__ import annotations

import asyncio
import csv
import logging
import time
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Set

from .amounts import ZERO, Asset
from .config import REPORTS_DIR, Settings, blocked_reason, load_blocklist, load_token_entries, load_token_pairs
from .db import DB, now_iso
from .market import MarketFeed
from .routes import Route, build_routes
from .scanner import scan
from .xrplnet import Net

log = logging.getLogger(__name__)

SUSPICIOUS_SCANS = 75  # ~5 minutes at one scan per 4 seconds


def _candidate_tokens(network: str) -> tuple[List[Asset], Set[str]]:
    """All tokens on the list (approved or not), plus the set whose review
    verdict wasn't 'NOT RECOMMENDED' - so the report can say what's realistic."""
    tokens, safe = [], set()
    blocked = load_blocklist()
    for t in load_token_entries(network):
        a = Asset(t["currency"], t["issuer"])
        if blocked_reason(a, blocked):
            continue
        tokens.append(a)
        if t.get("verdict") != "NOT RECOMMENDED":
            safe.add(a.key)
    return tokens, safe


async def run_watch(settings: Settings, db: Optional[DB] = None, max_scans: Optional[int] = None) -> None:
    db = db or DB()
    net = Net(
        settings.rpc_urls, network=settings.network, max_concurrency=6,
        rate_per_server=settings.requests_per_second_per_server,
    )
    feed = MarketFeed(net, depth=settings.book_depth)
    tokens, safe = _candidate_tokens(settings.network)
    if not tokens:
        raise SystemExit("No candidate tokens. Run `discover` first.")
    allowed = set(tokens)  # candidates minus the block list
    pairs = [(a, b) for a, b in load_token_pairs(settings.network) if a in allowed and b in allowed]
    netname = f"{settings.network}-watch"
    open_eps: Dict[str, dict] = {}  # route key -> running episode
    routes: List[Route] = []
    last_routes = 0.0
    scans = 0
    db.event(netname, "watch", "INFO", "start", f"watching {len(tokens)} tokens, {len(pairs)} token/token markets")
    while max_scans is None or scans < max_scans:
        t0 = time.time()
        try:
            snap = await feed.snapshot(tokens, pairs)
        except Exception as e:
            log.warning("snapshot failed: %s", e)
            await asyncio.sleep(10)
            continue
        if time.time() - last_routes > 600:
            routes = build_routes(snap.pairs(), settings.min_hops, settings.max_hops)
            last_routes = time.time()
        opps = scan(
            routes,
            snap,
            max_size=settings.watch_max_size_xrp,
            min_size=settings.min_trade_xrp,
            min_profit=settings.min_profit_xrp,
            slippage_buffer=settings.slippage_buffer,
            max_fee_drops=settings.max_network_fee_drops,
        )
        seen_now: Set[str] = set()
        n_prof = 0
        ts = now_iso()
        for o in opps:
            if o.net_profit <= 0:
                continue  # gaps that can't even pay the network fee aren't opportunities
            all_safe = all(t.key in safe for t in o.route.tokens)
            db.opportunity(
                network=netname,
                bot="watch",
                route=o.route.key,
                hops=o.route.hops,
                size_xrp=o.size_xrp,
                gross_profit_xrp=o.gross_profit,
                network_fee_xrp=o.network_fee_xrp,
                net_profit_xrp=o.net_profit,
                safe_profit_xrp=o.safe_profit,
                action="watch-executable" if o.executable else "watch-missed",
                reason=o.reason,
                ledger_index=o.ledger_index,
            )
            if not o.executable:
                continue
            n_prof += 1
            seen_now.add(o.route.key)
            ep = open_eps.get(o.route.key)
            if ep:
                ep["max_net"] = max(ep["max_net"], o.net_profit)
                if o.safe_profit > ep["max_safe"]:
                    ep["max_safe"], ep["size"] = o.safe_profit, o.size_xrp
                db.execute(
                    "UPDATE watch_episodes SET last_seen=?, scans=scans+1, ledger_last=?, max_net_profit_xrp=?, "
                    "max_safe_profit_xrp=?, best_size_xrp=? WHERE id=?",
                    (ts, o.ledger_index, str(ep["max_net"]), str(ep["max_safe"]), str(ep["size"]), ep["id"]),
                )
            else:
                row_id = db._insert(
                    "watch_episodes",
                    dict(
                        network=netname, route=o.route.key, hops=o.route.hops, first_seen=ts, last_seen=ts, scans=1,
                        ledger_first=o.ledger_index, ledger_last=o.ledger_index,
                        max_net_profit_xrp=str(o.net_profit), max_safe_profit_xrp=str(o.safe_profit),
                        best_size_xrp=str(o.size_xrp), all_tokens_pass=int(all_safe), open=1,
                    ),
                )
                open_eps[o.route.key] = {"id": row_id, "max_net": o.net_profit, "max_safe": o.safe_profit, "size": o.size_xrp}
        for key in list(open_eps):
            if key not in seen_now:
                db.execute("UPDATE watch_episodes SET open=0 WHERE id=?", (open_eps.pop(key)["id"],))
        dur = int((time.time() - t0) * 1000)
        db._insert(
            "watch_scans",
            dict(ts=ts, network=netname, ledger_index=snap.ledger_index, routes_checked=len(routes),
                 profitable=n_prof, duration_ms=dur),
        )
        scans += 1
        if scans % 100 == 0:
            log.info("watch: %d scans, %d routes, last scan %d ms", scans, len(routes), dur)
        await asyncio.sleep(max(0.5, settings.scan_interval_seconds - (time.time() - t0)))


def watch_report(db: DB, network: str = "mainnet", out_dir: Path = REPORTS_DIR) -> Path:
    netname = f"{network}-watch"
    scans = db.query("SELECT ts, ledger_index, profitable FROM watch_scans WHERE network=? ORDER BY ts", (netname,))
    eps = db.query("SELECT * FROM watch_episodes WHERE network=? ORDER BY first_seen", (netname,))
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = ["# Mainnet watch-only report", ""]
    if not scans:
        lines.append("No scans recorded yet.")
        p = out_dir / f"watch_report_{network}.md"
        p.write_text("\n".join(lines))
        return p

    first, last = scans[0]["ts"], scans[-1]["ts"]
    t_first = datetime.fromisoformat(first.rstrip("Z"))
    t_last = datetime.fromisoformat(last.rstrip("Z"))
    hours = max((t_last - t_first).total_seconds() / 3600, 1e-9)
    ledgers = (scans[-1]["ledger_index"] or 0) - (scans[0]["ledger_index"] or 0)

    def dec(r, k):
        return Decimal(r[k] or "0")

    # A gap that sits untouched for many minutes on mainnet is suspicious:
    # other bots would have taken it if it were real. Usually something we
    # can't see from order books blocks it (frozen trust lines, authorisation,
    # rounding at tiny sizes). We list these but leave them out of the totals.
    suspicious = [e for e in eps if e["scans"] > SUSPICIOUS_SCANS]
    capturable = [e for e in eps if 2 <= e["scans"] <= SUSPICIOUS_SCANS]
    capturable_safe = [e for e in capturable if e["all_tokens_pass"]]
    one_shot = [e for e in eps if e["scans"] < 2]
    total_cap = sum((dec(e, "max_safe_profit_xrp") for e in capturable_safe), ZERO)
    per_day = (
        f"≈ **{total_cap / Decimal(hours / 24):.4f} XRP/day**"
        if hours >= 24
        else "(per-day rate shown once there's at least 24 hours of data)"
    )

    lines += [
        f"Period: {first} → {last} ({hours:.1f} hours, {len(scans)} scans, ~{ledgers} ledgers).",
        "",
        "## The short answer",
        "",
        f"- Profitable-after-all-costs gaps seen: **{len(eps)}** episodes.",
        f"- Of those, gaps that lasted long enough for us to realistically catch (≥2 scans): **{len(capturable)}**.",
        f"- …and that only used tokens that passed the safety review: **{len(capturable_safe)}**.",
        f"- Gaps gone within one scan (taken by someone faster, or prices moved): **{len(one_shot)}**.",
        f"- Gaps that stayed open suspiciously long (>{SUSPICIOUS_SCANS} scans, probably not really tradeable, "
        f"excluded below): **{len(suspicious)}**.",
        f"- Best-case profit if we had caught every catchable, safe gap at its best size (≤ 100 XRP): "
        f"**{total_cap:.4f} XRP** total {per_day}.",
        "",
        "This best case is an UPPER BOUND: it assumes we always land first. Real bots compete for the same gaps, "
        "so real results would be lower.",
        "",
    ]
    hops = Counter(e["hops"] for e in capturable_safe)
    if hops:
        lines += ["## By loop length", ""] + [f"- {h} hops: {n}" for h, n in sorted(hops.items())] + [""]

    by_route: Dict[str, List] = defaultdict(list)
    for e in capturable_safe:
        by_route[e["route"]].append(e)
    top = sorted(by_route.items(), key=lambda kv: -sum(dec(e, "max_safe_profit_xrp") for e in kv[1]))[:15]
    if top:
        lines += ["## Best routes", "", "| Route | Episodes | Total best-case profit (XRP) | Typical size (XRP) |", "|---|---|---|---|"]
        for key, es in top:
            label = Route.from_key(key).label
            tot = sum((dec(e, "max_safe_profit_xrp") for e in es), ZERO)
            sizes = sorted(dec(e, "best_size_xrp") for e in es)
            lines.append(f"| {label} | {len(es)} | {tot:.4f} | {sizes[len(sizes) // 2]:.2f} |")
        lines.append("")

    # How many gaps were open at the same moment? This tells us whether more
    # than one bot would ever help.
    ledger_counts: Counter = Counter()
    for e in capturable_safe:
        for li in range((e["ledger_first"] or 0), (e["ledger_last"] or 0) + 1):
            ledger_counts[li] += 1
    if ledger_counts:
        mx = max(ledger_counts.values())
        multi = sum(1 for v in ledger_counts.values() if v >= 2)
        lines += [
            "## Would more bots help?",
            "",
            f"- Most catchable gaps open at the same moment: **{mx}**.",
            f"- Ledgers where 2+ gaps were open at once: **{multi}** of {len(ledger_counts)} ledgers that had any.",
            "- If this is usually 1, a single bot catches nearly everything and extra bots only add reserve costs.",
            "",
        ]

    missed = db.query(
        "SELECT reason, COUNT(*) n FROM opportunities WHERE network=? AND action='watch-missed' GROUP BY reason ORDER BY n DESC LIMIT 10",
        (netname,),
    )
    if missed:
        lines += ["## Gaps that existed but would NOT have been worth trading", ""]
        lines += [f"- {r['n']}× {r['reason']}" for r in missed]
        lines.append("")

    p = out_dir / f"watch_report_{network}.md"
    p.write_text("\n".join(lines))
    with open(out_dir / f"watch_episodes_{network}.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["first_seen", "last_seen", "scans", "route", "hops", "best_size_xrp", "max_net_profit_xrp",
                    "max_safe_profit_xrp", "tokens_passed_review"])
        for e in eps:
            w.writerow([e["first_seen"], e["last_seen"], e["scans"], Route.from_key(e["route"]).label, e["hops"],
                        e["best_size_xrp"], e["max_net_profit_xrp"], e["max_safe_profit_xrp"], bool(e["all_tokens_pass"])])
    return p
