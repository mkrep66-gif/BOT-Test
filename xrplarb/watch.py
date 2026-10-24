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
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Set

from .amounts import ZERO, Asset
from .config import REPORTS_DIR, Settings, blocked_reason, load_blocklist, load_token_entries, load_token_pairs
from .alerts import Alerter
from .db import DB, now_iso
from .backrun import PaperBackrunner
from .livefeed import LiveFeed
from .localtime import day_start_utc, local_tz, next_day, tz_label
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


def _approved_keys(network: str) -> Set[str]:
    """Tokens the bots would actually trade: approved and not blocked."""
    blocked = load_blocklist()
    return {
        Asset(t["currency"], t["issuer"]).key
        for t in load_token_entries(network)
        if t.get("approved") and not blocked_reason(Asset(t["currency"], t["issuer"]), blocked)
    }


async def run_watch(settings: Settings, db: Optional[DB] = None, max_scans: Optional[int] = None) -> None:
    db = db or DB()
    net = Net(
        settings.rpc_urls, network=settings.network, max_concurrency=6,
        rate_per_server=settings.requests_per_second_per_server,
    )
    feed = MarketFeed(net, depth=settings.book_depth)
    tokens, safe = _candidate_tokens(settings.network)
    approved = _approved_keys(settings.network)
    alerter = Alerter(settings.network, db)
    if not tokens:
        raise SystemExit("No candidate tokens. Run `discover` first.")
    allowed = set(tokens)  # candidates minus the block list
    pairs = [(a, b) for a, b in load_token_pairs(settings.network) if a in allowed and b in allowed]
    # Live feed: the servers push every change and we scan each ledger as it
    # closes. If it isn't healthy, fall back to reading every market in full.
    live = LiveFeed(net, settings.ws_urls, depth=settings.book_depth) if settings.use_ledger_stream and settings.ws_urls else None
    live_task = None
    backrun = backrun_task = None
    if live:
        live.configure(tokens, pairs)
        live_task = asyncio.create_task(live.run())
        # paper backrunning: gaps a pending trade is about to open (see backrun.py)
        backrun = PaperBackrunner(live, db, settings, settings.network, approved, lambda: routes)
        backrun_task = asyncio.create_task(backrun.run())
    started = last_stats = time.time()
    netname = f"{settings.network}-watch"
    open_eps: Dict[str, dict] = {}  # route key -> running episode
    routes: List[Route] = []
    last_routes = 0.0
    scans = 0
    db.event(netname, "watch", "INFO", "start", f"watching {len(tokens)} tokens, {len(pairs)} token/token markets")
    while max_scans is None or scans < max_scans:
        t0 = time.time()
        snap = await live.next_snapshot(timeout=10) if live else None
        is_live = snap is not None
        if snap is None and live and time.time() - started < 120:
            continue  # the live feed is still starting up (first full read): wait for it
        if snap is None:
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
            quick_reject=True,
        )
        seen_now: Set[str] = set()
        n_prof = 0
        ts = now_iso()
        for o in opps:
            if o.net_profit <= 0:
                continue  # gaps that can't even pay the network fee aren't opportunities
            all_safe = all(t.key in safe for t in o.route.tokens)
            would = all(t.key in approved for t in o.route.tokens)
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
                        first_safe_profit_xrp=str(o.safe_profit), would_trade=int(would),
                    ),
                )
                open_eps[o.route.key] = {"id": row_id, "max_net": o.net_profit, "max_safe": o.safe_profit, "size": o.size_xrp}
        for key in list(open_eps):
            if key not in seen_now:
                db.execute("UPDATE watch_episodes SET open=0 WHERE id=?", (open_eps.pop(key)["id"],))
        # live: time from the ledger closing to our decision; full read: the whole read + scan
        dur = int((time.time() - (snap.taken_at if is_live else t0)) * 1000)
        db._insert(
            "watch_scans",
            dict(ts=ts, network=netname, ledger_index=snap.ledger_index, routes_checked=len(routes),
                 profitable=n_prof, duration_ms=dur, live=int(is_live)),
        )
        scans += 1
        if scans % 100 == 0:
            log.info("watch: %d scans, %d routes, last scan %d ms", scans, len(routes), dur)
        if live and time.time() - last_stats > 3600:
            last_stats = time.time()
            log.info("live feed: %s; paper backrun: %s", live.stats, backrun.stats if backrun else "-")
            if live.stats["drift"]:
                db.event(netname, "watch", "WARN", "live-drift", f"live feed corrected itself {live.stats['drift']} time(s)")
        await maybe_send_daily(db, settings.network, alerter)
        if not is_live:  # full reads: don't hammer the servers while the live feed recovers
            await asyncio.sleep(max(0.5, settings.scan_interval_seconds - (time.time() - t0)))
    if live:
        live.stop()
        live_task.cancel()
    if backrun:
        backrun.stop()
        backrun_task.cancel()


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


# ---- daily paper-trading report ------------------------------------------------

DAILY_SEND_HOUR = 7.5  # local time: 07:30


def episode_profit(e) -> Decimal:
    """A would-be trade's profit: what the gap showed when first seen (when a bot would fire)."""
    return Decimal(e["first_safe_profit_xrp"] or e["max_safe_profit_xrp"] or "0")


def paper_day(db: DB, network: str, day: str) -> dict:
    """One local day of paper trading, counted the same way everywhere (daily
    report, Telegram /paper, dashboard). See daily_report for the rules."""
    netname = f"{network}-watch"
    start, end = day_start_utc(day), day_start_utc(next_day(day))
    eps = db.query("SELECT * FROM watch_episodes WHERE network=? AND first_seen>=? AND first_seen<?", (netname, start, end))
    scans = db.query(
        "SELECT COUNT(*) n, SUM(COALESCE(live, 0)) l, AVG(CASE WHEN live=1 THEN duration_ms END) ld, "
        "AVG(CASE WHEN COALESCE(live, 0)=0 THEN duration_ms END) fd "
        "FROM watch_scans WHERE network=? AND ts>=? AND ts<?", (netname, start, end)
    )[0]
    ours = [e for e in eps if e["would_trade"]]
    d = dict(
        episodes=eps,
        scans=scans,
        traded=[e for e in ours if 2 <= e["scans"] <= SUSPICIOUS_SCANS],
        too_fast=[e for e in ours if e["scans"] < 2],
        stuck=[e for e in ours if e["scans"] > SUSPICIOUS_SCANS],
        others=[e for e in eps if not e["would_trade"] and 2 <= e["scans"] <= SUSPICIOUS_SCANS],
    )
    for k, src in (("profit", "traded"), ("lost_to_speed", "too_fast"), ("other_profit", "others")):
        d[k] = sum((episode_profit(e) for e in d[src]), ZERO)
    # Gaps come in bursts: one big trade opens many loops at once through the
    # same pools, and taking one closes the others. Count each burst (gaps
    # first seen in the same ledger) once, at its best loop - the realistic number.
    for k, src in (("bursts", "traded"), ("bursts_missed", "too_fast"), ("bursts_other", "others")):
        best: Dict[int, Decimal] = {}
        for e in d[src]:
            li = e["ledger_first"] or 0
            best[li] = max(best.get(li, ZERO), episode_profit(e))
        d[k] = len(best)
        d[k + "_profit"] = sum(best.values(), ZERO)
    # paper backrunning (gaps a pending trade was about to open), approved tokens only
    br = db.query(
        "SELECT ledger_target, safe_profit_xrp, decided_ms FROM backrun_paper WHERE network=? AND ts>=? AND ts<? "
        "AND would_trade=1", (f"{network}-backrun", start, end))
    best_br: Dict[int, Decimal] = {}
    for r in br:
        best_br[r["ledger_target"]] = max(best_br.get(r["ledger_target"], ZERO), Decimal(r["safe_profit_xrp"] or "0"))
    d["backrun"], d["backrun_loops"] = len(best_br), len(br)
    d["backrun_profit"] = sum(best_br.values(), ZERO)
    d["backrun_ms"] = int(sum(r["decided_ms"] or 0 for r in br) / len(br)) if br else None
    return d


def daily_report(db: DB, network: str, day: str) -> tuple[str, str]:
    """Paper-trading results for one local day. Returns (full markdown, short
    Telegram text).

    A gap that's still there on the next scan is counted as a trade we would
    have made (our transaction lands a ledger or two after we see the gap),
    at the profit it showed when first seen - after network, pool and issuer
    fees and our slippage margin. A gap gone by the next scan is counted as
    missed on speed."""
    d = paper_day(db, network, day)
    eps, scans, traded, too_fast, stuck, others = (d[k] for k in ("episodes", "scans", "traded", "too_fast", "stuck", "others"))
    profit, lost_to_speed, other_profit = d["bursts_profit"], d["bursts_missed_profit"], d["bursts_other_profit"]
    n_tr, n_fast, n_oth = d["bursts"], d["bursts_missed"], d["bursts_other"]
    first = episode_profit

    by_route: Dict[str, List] = defaultdict(list)
    for e in traded:
        by_route[e["route"]].append(e)
    best = sorted(by_route.items(), key=lambda kv: -sum(first(e) for e in kv[1]))[:5]
    fast_routes = Counter(e["route"] for e in too_fast).most_common(5)

    def label(k: str) -> str:
        try:
            return Route.from_key(k).label
        except Exception:
            return k

    L = [
        f"# Paper trading on mainnet: {day} ({tz_label()})",
        "",
        "Read-only: live mainnet prices, the bots' own rules, approved tokens only. No keys, no XRP spent.",
        "",
        f"- **Would-be trades:** {n_tr} (from {len(traded)} profitable loops - a burst of loops through the same "
        "pools counts once, at its best loop; all-or-nothing, so each is a win or a cancelled no-fill - never a loss)",
        f"- **Profit after all fees:** {profit:+.4f} XRP" + (f" (average {profit / n_tr:+.4f} per trade)" if n_tr else ""),
        f"- **Gaps that closed before we could act (speed):** {n_fast} ({len(too_fast)} loops), worth {lost_to_speed:+.4f} XRP",
        f"- Gaps on watch-only (unapproved) tokens we'd have caught: {n_oth} ({len(others)} loops), worth {other_profit:+.4f} XRP",
        f"- Gaps that stayed open suspiciously long (left out - probably not really tradeable): {len(stuck)}",
        f"- **Backrun chances (gaps a pending trade was about to open):** {d['backrun']}"
        + (f" ({d['backrun_loops']} loops), worth {d['backrun_profit']:+.4f} XRP, decided {d['backrun_ms']} ms after "
           "seeing the trade - check them with paper-verify" if d["backrun"] else ""),
        f"- Scans: {scans['n'] or 0}"
        + (f"; {scans['l']} on the live feed, deciding on average {(scans['ld'] or 0) / 1000:.2f} s after each ledger closed"
           if scans["l"] else "")
        + (f"; {(scans['n'] or 0) - (scans['l'] or 0)} full reads, average {(scans['fd'] or 0) / 1000:.1f} s each"
           if (scans["n"] or 0) > (scans["l"] or 0) else ""),
        "",
    ]
    if best:
        L += ["## Best routes", "", "| Route | Trades | Profit (XRP) |", "|---|---|---|"]
        L += [f"| {label(k)} | {len(es)} | {sum(first(e) for e in es):+.4f} |" for k, es in best]
        L.append("")
    if fast_routes:
        L += ["## Where speed cost us (gaps gone by the next scan)", "", "| Route | Gaps missed |", "|---|---|"]
        L += [f"| {label(k)} | {n} |" for k, n in fast_routes]
        L.append("")
    if not eps:
        L += ["No profitable gaps at all this day.", ""]
    short = (
        f"Paper trading {day}: {n_tr} would-be trades, {profit:+.4f} XRP after fees. "
        f"Missed on speed: {n_fast} ({lost_to_speed:+.4f} XRP). "
        f"Unapproved tokens: {n_oth} ({other_profit:+.4f} XRP). "
        f"Backrun chances: {d['backrun']} ({d['backrun_profit']:+.4f} XRP). "
        + (f"Best: {label(best[0][0])} ({sum(first(e) for e in best[0][1]):+.4f})." if best else "")
    )
    return "\n".join(L), short


def save_daily_report(db: DB, network: str, day: str, out_dir: Optional[Path] = None) -> tuple[Path, str]:
    text, short = daily_report(db, network, day)
    out_dir = out_dir or REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"paper_{network}_{day}.md"
    p.write_text(text)
    return p, short


async def maybe_send_daily(db: DB, network: str, alerter: Alerter) -> None:
    """Once a day, after 07:30 local, report yesterday (file + Telegram)."""
    now = datetime.now(local_tz())
    if now.hour + now.minute / 60 < DAILY_SEND_HOUR:
        return
    yesterday = (now.date() - timedelta(days=1)).isoformat()
    flag = f"paper_daily_sent:{network}"
    if db.get_flag(flag) == yesterday:
        return
    db.set_flag(flag, yesterday)
    start, end = day_start_utc(yesterday), day_start_utc(next_day(yesterday))
    if not db.query("SELECT 1 FROM watch_scans WHERE network=? AND ts>=? AND ts<? LIMIT 1", (f"{network}-watch", start, end)):
        return  # wasn't running that day: nothing to report
    try:
        p, short = save_daily_report(db, network, yesterday)
        await alerter.send("Daily paper-trading report", short + f"\nFull report on the server: {p}", force=True)
    except Exception as e:  # a report problem must never stop the watcher
        log.warning("daily report failed: %s", e)
