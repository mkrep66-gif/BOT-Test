"""Check paper trading's gaps against what really happened on the ledger.

For every gap paper trading saw on a day, look at the ledger right after the
gap was last seen (that's where it closed) and ask: did someone trade through
the markets of that loop?

- an arbitrage through 2+ of the loop's markets -> the gap was real, and
  another bot took it (so it was catchable - by them, at least);
- one ordinary trade in one of its markets     -> prices moved; the gap was
  probably real but closed by normal trading;
- nothing at all touched its markets           -> the gap vanished without a
  trade: a sign it was never really there (a "phantom", e.g. an order whose
  owner didn't have the funds).

Read-only: public ledger data, no keys.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Set, Tuple

from xrpl.models.requests import GenericRequest

from .amounts import XRP, Asset
from .db import DB
from .livefeed import _asset_of
from .localtime import day_start_utc, next_day
from .routes import Route
from .xrplnet import Net, _cur

Market = Tuple[str, str]  # unordered pair of asset keys


def _market(a: Asset, b: Asset) -> Market:
    return (a.key, b.key) if a.key <= b.key else (b.key, a.key)


def route_markets(route_key: str) -> List[Market]:
    r = Route.from_key(route_key)
    return [_market(a, b) for a, b in zip(r.assets, r.assets[1:])]


def tx_markets(meta: dict, pools: Dict[str, Market]) -> Set[Market]:
    """Markets a transaction traded in: AMM pools whose account it changed,
    and order books one of whose offers it changed."""
    out: Set[Market] = set()
    for wrapper in meta.get("AffectedNodes", []):
        kind, node = next(iter(wrapper.items()))
        f = node.get("FinalFields") or node.get("NewFields") or {}
        typ = node.get("LedgerEntryType")
        if typ == "AccountRoot" and f.get("Account") in pools:
            out.add(pools[f["Account"]])
        elif typ == "RippleState":
            for side in ("LowLimit", "HighLimit"):
                acct = (f.get(side) or {}).get("issuer")
                if acct in pools:
                    out.add(pools[acct])
        elif typ == "Offer" and "TakerGets" in f and "TakerPays" in f:
            out.add(_market(_asset_of(f["TakerGets"]), _asset_of(f["TakerPays"])))
    return out


def classify(markets: List[Market], txs: Iterable[dict], pools: Dict[str, Market]) -> Tuple[str, Optional[str]]:
    """'arbitrage' (someone went through 2+ of the loop's markets in one
    transaction), 'trade' (only single-market trades), or 'none'. Also returns
    the account behind the arbitrage, if any."""
    want = set(markets)
    best = "none"
    who = None
    for tx in txs:
        meta = tx.get("meta") or tx.get("metaData") or {}
        if meta.get("TransactionResult") != "tesSUCCESS":
            continue
        hit = tx_markets(meta, pools) & want
        if len(hit) >= 2:
            return "arbitrage", tx.get("Account") or (tx.get("tx_json") or {}).get("Account")
        if hit:
            best = "trade"
    return best, who


async def _pool_accounts(net: Net, markets: Set[Market]) -> Dict[str, Market]:
    pools: Dict[str, Market] = {}

    async def one(m: Market):
        a, b = (Asset.from_key(k) for k in m)
        try:
            amm = await net.amm_info(a, b)
        except Exception:
            amm = None
        if amm and amm.get("account"):
            pools[amm["account"]] = m

    await asyncio.gather(*(one(m) for m in markets))
    return pools


async def _ledger_txs(net: Net, index: int, cache: Dict[int, List[dict]]) -> List[dict]:
    if index not in cache:
        r = await net.request(GenericRequest(method="ledger", ledger_index=index, transactions=True, expand=True))
        cache[index] = (r.get("ledger") or {}).get("transactions") or []
    return cache[index]


async def verify_day(db: DB, net: Net, network: str, day: str, look_ahead: int = 2) -> Dict:
    netname = f"{network}-watch"
    start, end = day_start_utc(day), day_start_utc(next_day(day))
    eps = db.query(
        "SELECT * FROM watch_episodes WHERE network=? AND first_seen>=? AND first_seen<? AND scans<=75 "
        "AND ledger_last IS NOT NULL ORDER BY first_seen", (netname, start, end))
    markets = {m for e in eps for m in route_markets(e["route"])}
    pools = await _pool_accounts(net, markets)
    cache: Dict[int, List[dict]] = {}
    results = []
    for e in eps:
        ms = route_markets(e["route"])
        verdict, who = "none", None
        # the gap was still there at ledger_last; it closed in one of the next ledgers
        for li in range(e["ledger_last"] + 1, e["ledger_last"] + 1 + look_ahead):
            try:
                txs = await _ledger_txs(net, li, cache)
            except Exception:
                verdict = "unknown"
                break
            v, w = classify(ms, txs, pools)
            if v == "arbitrage":
                verdict, who = v, w
                break
            if v == "trade":
                verdict = "trade"
        kind = "would-be" if e["would_trade"] and e["scans"] >= 2 else "missed" if e["would_trade"] else "unapproved"
        results.append({"route": e["route"], "kind": kind, "verdict": verdict, "who": who,
                        "profit": Decimal(e["first_safe_profit_xrp"] or e["max_safe_profit_xrp"] or "0")})
    return {"day": day, "results": results, "ledgers_read": len(cache)}


def report(v: Dict) -> str:
    res = v["results"]
    lines = [f"# Paper gaps checked against the real ledger: {v['day']}", "",
             f"Read-only check of {len(res)} gaps ({v['ledgers_read']} ledgers read). For each gap: what happened in "
             "its markets in the ledger(s) right after it was last seen.", ""]
    if not res:
        return "\n".join(lines + ["No gaps that day."])
    names = {"arbitrage": "another bot arbitraged the same loop (real gap)",
             "trade": "an ordinary trade moved a price (probably real)",
             "none": "no trade at all in its markets (possible phantom)",
             "unknown": "couldn't read the ledger"}
    for kind, title in (("would-be", "Would-be trades (approved tokens)"), ("missed", "Missed on speed"),
                        ("unapproved", "Unapproved tokens")):
        group = [r for r in res if r["kind"] == kind]
        if not group:
            continue
        c = Counter(r["verdict"] for r in group)
        lines += [f"## {title}: {len(group)}", ""]
        for verdict in ("arbitrage", "trade", "none", "unknown"):
            if c[verdict]:
                worth = sum(r["profit"] for r in group if r["verdict"] == verdict)
                lines.append(f"- {c[verdict]} - {names[verdict]}, worth {worth:+.4f} XRP")
        rivals = Counter(r["who"] for r in group if r["who"])
        if rivals:
            lines.append("- Who took them: " + ", ".join(f"{w[:8]}… ×{n}" for w, n in rivals.most_common(5)))
        lines.append("")
    real = [r for r in res if r["verdict"] in ("arbitrage", "trade")]
    lines += ["## Bottom line", "",
              f"- {len(real)} of {len(res)} gaps were followed by real trading in their markets.",
              f"- {sum(1 for r in res if r['verdict'] == 'none')} vanished with no trade at all - treat those as doubtful.",
              ""]
    return "\n".join(lines)
