"""Day-by-day results in your local time, and the weekly review.

Two kinds of profit are shown, because they answer different questions:
  * trade profit: the sum of each trade's own result (what the bots did)
  * wallet change: total wallet value at your local midnight vs the next one,
    with tokens priced at what they'd really sell for and your own deposits /
    sweeps to the vault left out. This is the number the loss cap uses.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, List, Optional

from .db import DB
from .localtime import day_start_utc, next_day, today_local, tz_label

ZERO = Decimal(0)
# trade statuses that are attempts at a loop (not conversions / housekeeping)
ATTEMPTS = ("win", "loss", "failed")


def _d(v) -> Decimal:
    return Decimal(str(v)) if v not in (None, "") else ZERO


@dataclass
class DayResult:
    day: str
    attempts: int = 0
    wins: int = 0
    losses: int = 0
    no_fills: int = 0  # sent, but cancelled by the ledger (only the fee was paid)
    trade_profit: Decimal = ZERO
    fees: Decimal = ZERO
    wallet_change: Optional[Decimal] = None

    @property
    def win_rate(self) -> Optional[float]:
        return self.wins / self.attempts * 100 if self.attempts else None


def _value_at(db: DB, network: str, bot: str, before_iso: str) -> Optional[Decimal]:
    rows = db.query(
        "SELECT total_value_xrp FROM balances WHERE network=? AND bot=? AND ts<? ORDER BY ts DESC LIMIT 1",
        (network, bot, before_iso),
    )
    return _d(rows[0]["total_value_xrp"]) if rows else None


def _first_value_in(db: DB, network: str, bot: str, start: str, end: str) -> Optional[Decimal]:
    rows = db.query(
        "SELECT total_value_xrp FROM balances WHERE network=? AND bot=? AND ts>=? AND ts<? ORDER BY ts ASC LIMIT 1",
        (network, bot, start, end),
    )
    return _d(rows[0]["total_value_xrp"]) if rows else None


def day_result(db: DB, network: str, day: str) -> DayResult:
    start, end = day_start_utc(day), day_start_utc(next_day(day))
    r = DayResult(day=day)
    for t in db.query(
        "SELECT status, profit_xrp, network_fee_xrp FROM trades WHERE network=? AND ts>=? AND ts<?",
        (network, start, end),
    ):
        r.fees += _d(t["network_fee_xrp"])
        r.trade_profit += _d(t["profit_xrp"])
        if t["status"] in ATTEMPTS:
            r.attempts += 1
            r.wins += t["status"] == "win"
            r.losses += t["status"] == "loss"
            r.no_fills += t["status"] == "failed"
    change, any_bot = ZERO, False
    for row in db.query("SELECT DISTINCT bot FROM balances WHERE network=?", (network,)):
        bot = row["bot"]
        v0 = _value_at(db, network, bot, start) or _first_value_in(db, network, bot, start, end)
        v1 = _value_at(db, network, bot, end)
        if v0 is None or v1 is None:
            continue
        any_bot = True
        change += v1 - v0 - db.net_transfers_since(network, bot, start, end)
    r.wallet_change = change if any_bot else None
    return r


def daily_history(db: DB, network: str, days: int = 14, until: Optional[str] = None) -> List[DayResult]:
    """Most recent day first. Days with no activity at all are left out."""
    last = date.fromisoformat(until or today_local())
    out = []
    for i in range(days):
        r = day_result(db, network, (last - timedelta(days=i)).isoformat())
        if r.attempts or r.fees or r.wallet_change:
            out.append(r)
    return out


# ---- weekly review ---------------------------------------------------------------

@dataclass
class Review:
    network: str
    first_day: str
    last_day: str
    days: List[DayResult]
    attempts: int = 0
    wins: int = 0
    losses: int = 0
    no_fills: int = 0
    trade_profit: Decimal = ZERO
    fees: Decimal = ZERO
    wallet_change: Optional[Decimal] = None
    expected_profit: Decimal = ZERO
    no_fill_reasons: Counter = field(default_factory=Counter)
    missed_reasons: Counter = field(default_factory=Counter)
    problems: List[str] = field(default_factory=list)
    server_trouble: Dict[str, List[int]] = field(default_factory=dict)  # url -> [requests, failures, backoffs]
    suggestions: List[str] = field(default_factory=list)


def _no_fill_reason(code: str) -> str:
    code = code or ""
    if code.startswith("inner-unknown"):
        return "confirmed, but its details couldn't be read (profit still counted via wallet balances)"
    if code.startswith("batch-cancelled"):
        return "price moved or someone else took the gap first (ledger cancelled the loop)"
    if code.startswith("expired") or code.startswith("timeout"):
        return "didn't make it into a ledger in time"
    if code.startswith("tec") or code.startswith("tem") or code.startswith("tef") or code.startswith("tel"):
        return f"ledger rejected it ({code.split(':')[0]})"
    if code.startswith("error:"):
        return "connection problem while sending"
    return code or "unknown"


def weekly_review(db: DB, network: str, days: int = 7, until: Optional[str] = None) -> Review:
    last = until or today_local()
    first = (date.fromisoformat(last) - timedelta(days=days - 1)).isoformat()
    start, end = day_start_utc(first), day_start_utc(next_day(last))
    rv = Review(network=network, first_day=first, last_day=last, days=daily_history(db, network, days, last))
    changes = [d.wallet_change for d in rv.days if d.wallet_change is not None]
    rv.wallet_change = sum(changes, ZERO) if changes else None
    for d in rv.days:
        rv.attempts += d.attempts
        rv.wins += d.wins
        rv.losses += d.losses
        rv.no_fills += d.no_fills
        rv.trade_profit += d.trade_profit
        rv.fees += d.fees
    for t in db.query(
        "SELECT status, result_code, expected_profit_xrp FROM trades WHERE network=? AND ts>=? AND ts<?",
        (network, start, end),
    ):
        if t["status"] in ATTEMPTS:
            rv.expected_profit += _d(t["expected_profit_xrp"])
        if t["status"] == "failed":
            rv.no_fill_reasons[_no_fill_reason(t["result_code"])] += 1
    for o in db.query(
        "SELECT reason, COUNT(*) n FROM opportunities WHERE network=? AND action='missed' AND ts>=? AND ts<? GROUP BY reason",
        (network, start, end),
    ):
        rv.missed_reasons[_group_reason(o["reason"])] += o["n"]
    for e in db.query(
        "SELECT kind, level, message, bot FROM events WHERE (network=? OR network IS NULL) AND ts>=? AND ts<? ORDER BY ts",
        (network, start, end),
    ):
        if e["kind"] == "net-health":
            url, _, rest = (e["message"] or "").partition(" ")
            nums = dict(p.split("=") for p in rest.split() if "=" in p)
            agg = rv.server_trouble.setdefault(url, [0, 0, 0])
            agg[0] += int(nums.get("requests", 0))
            agg[1] += int(nums.get("failures", 0))
            agg[2] += int(nums.get("backoffs", 0))
        elif e["level"] in ("ERROR", "WARN") or e["kind"] in ("crash", "halt", "kill"):
            rv.problems.append(f"{e['bot'] or 'system'}: {e['kind']} - {e['message']}")
    rv.suggestions = _suggest(rv)
    return rv


def _group_reason(reason: str) -> str:
    """Collapse reasons that differ only in numbers (e.g. '... retry in 41s')."""
    r = reason or ""
    for prefix, label in (
        ("route paused after", "route paused after failed attempts (retry cooldown)"),
        ("prices were", "prices too old (over the 60-second limit)"),
        ("prices went stale", "prices went stale just before sending"),
        ("market in use by", "another bot was using that market"),
        ("bot not trading", "bot was stopped (kill switch / loss cap / halt)"),
        ("profit after fees and", "gap too small: profit after fees and safety margin below the minimum"),
    ):
        if r.startswith(prefix):
            return label
    return r


def _suggest(rv: Review) -> List[str]:
    out: List[str] = []
    if rv.attempts == 0:
        out.append("No trades were attempted. Check the bots were running and that some tokens are approved.")
        return out
    nf = rv.no_fills / rv.attempts
    moved = sum(n for k, n in rv.no_fill_reasons.items() if k.startswith("price moved"))
    if nf > 0.5 and moved >= rv.no_fills / 2:
        out.append(
            f"{nf:.0%} of attempts were cancelled by the ledger, mostly because prices moved first. Each one cost "
            "only the fee, but if that keeps happening, raising slippage_buffer a little (e.g. 0.0005 -> 0.001) "
            "means fewer, more reliable attempts."
        )
    if rv.missed_reasons.get("prices too old (over the 60-second limit)", 0) > 0:
        out.append("Some trades were skipped because prices were over 60 seconds old - a server was slow or out of sync.")
    if rv.missed_reasons.get("another bot was using that market", 0) > rv.attempts:
        out.append("Bots often wanted the same market at once. Fewer bots (num_bots) may do just as well.")
    if any(v[2] > 10 for v in rv.server_trouble.values()):
        out.append("A server told us to slow down more than 10 times. Lowering requests_per_second_per_server would help.")
    if rv.wins and rv.trade_profit > 0 and rv.expected_profit > 0 and rv.trade_profit < rv.expected_profit * Decimal("0.5"):
        out.append("Trades made under half the profit expected - the price model may be too optimistic; worth a closer look.")
    if not out:
        out.append("Nothing needs changing this week.")
    return out


def review_markdown(rv: Review) -> str:
    def x(v: Optional[Decimal], dp: int = 6) -> str:
        return "n/a" if v is None else f"{v:+.{dp}f} XRP"

    rate = f"{rv.wins / rv.attempts * 100:.0f}%" if rv.attempts else "n/a"
    L = [
        f"# Weekly review ({rv.network}): {rv.first_day} to {rv.last_day}",
        "",
        f"All times are your local time ({tz_label()}).",
        "",
        "## The short version",
        "",
        f"- **Trades attempted:** {rv.attempts}",
        f"- **Win rate:** {rate} ({rv.wins} wins, {rv.losses} losses, {rv.no_fills} no-fills)",
        f"- **Profit from trades:** {x(rv.trade_profit)} (after {rv.fees:.6f} XRP of network fees)",
        f"- **Change in wallet value:** {x(rv.wallet_change)} (tokens valued at what they'd really sell for; your own deposits and vault sweeps left out)",
        "",
        "A *no-fill* is a trade the ledger cancelled as a whole because the prices had changed by the time it "
        "arrived. Nothing was bought or sold; only the tiny network fee was paid.",
        "",
        "## Day by day",
        "",
        "| Day | Attempts | Wins | No-fills | Win rate | Trade profit | Wallet change |",
        "|---|---|---|---|---|---|---|",
    ]
    for d in rv.days:
        wr = f"{d.win_rate:.0f}%" if d.win_rate is not None else "-"
        L.append(f"| {d.day} | {d.attempts} | {d.wins} | {d.no_fills} | {wr} | {x(d.trade_profit)} | {x(d.wallet_change)} |")
    if not rv.days:
        L.append("| (no activity) | | | | | | |")
    L += ["", "## Why trades didn't fill", ""]
    L += [f"- {n} x {k}" for k, n in rv.no_fill_reasons.most_common()] or ["- No no-fills."]
    L += ["", "## Gaps seen but not traded (top reasons)", ""]
    L += [f"- {n} x {k}" for k, n in rv.missed_reasons.most_common(8)] or ["- None logged."]
    L += ["", "## Anything that broke", ""]
    if rv.problems:
        shown = rv.problems[-20:]
        L += [f"- {p}" for p in shown]
        if len(rv.problems) > len(shown):
            L.append(f"- ...and {len(rv.problems) - len(shown)} earlier warnings (see the dashboard's events list).")
    else:
        L.append("- Nothing. No crashes, halts or warnings.")
    if rv.server_trouble:
        L += ["", "## Ledger servers", "", "| Server | Requests | Failures | Times we backed off |", "|---|---|---|---|"]
        for url, (n, f, b) in rv.server_trouble.items():
            L.append(f"| {url} | {n} | {f} | {b} |")
    L += ["", "## Suggested tuning", ""]
    L += [f"- {s}" for s in rv.suggestions]
    L.append("")
    return "\n".join(L)
