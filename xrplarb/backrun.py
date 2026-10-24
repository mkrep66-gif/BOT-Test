"""Paper backrunning: spot gaps that a *pending* trade is about to open.

The top mainnet bot wins mostly by watching transactions that are waiting to
go into the next ledger, working out the gap a big trade will open, and
sending its loop into the same ledger behind it (reports/rival_rxLfrvic_*).
Reacting to closed ledgers can't see those gaps at all.

This watches the same thing, on paper: the server's stream of pending
("proposed") transactions. When one touches a market of ours, we read the
affected pools/books as they'll be AFTER it (the server's "current" ledger,
which already includes it), price the loops through them with the bots' own
rules, and log any that would pay. Nothing is sent; no keys, no XRP.

Each logged chance records the ledger it targets and the trade that would
open it, so paper-verify can later check against the real ledger whether that
trade landed and whether another bot took the gap.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import replace
from typing import Callable, Dict, List, Optional, Set, Tuple

from .amounts import ONE, XRP, Asset
from .db import DB, now_iso
from .livefeed import LiveFeed, _issue_json
from .market import amm_to_pool, offers_to_edge_offers
from .quote import Edge
from .routes import Route
from .scanner import scan

log = logging.getLogger(__name__)

# Transactions that can move a pool or an order book. Batches are left out:
# they're mostly other bots' own arbitrage attempts.
TRIGGER_TYPES = {"Payment", "OfferCreate", "AMMDeposit", "AMMWithdraw"}


def _amount_asset(v) -> Optional[Asset]:
    if isinstance(v, (str, int)):
        return XRP
    if isinstance(v, dict) and "currency" in v:
        if v["currency"] == "XRP":
            return XRP
        if "issuer" in v:
            return Asset(v["currency"], v["issuer"])
    return None


def tx_assets(tx: dict) -> Set[Asset]:
    """Every asset a transaction names: amounts, limits, AMM assets, path steps."""
    out: Set[Asset] = set()
    for k in ("Amount", "DeliverMax", "SendMax", "DeliverMin", "TakerGets", "TakerPays", "Asset", "Asset2",
              "Amount2", "EPrice"):
        a = _amount_asset(tx.get(k))
        if a is not None:
            out.add(a)
    for path in tx.get("Paths") or []:
        for step in path:
            cur = step.get("currency")
            if cur == "XRP":
                out.add(XRP)
            elif cur and step.get("issuer"):
                out.add(Asset(cur, step["issuer"]))
    return out


def touched_markets(tx: dict, markets: Set[Tuple[str, str]]) -> Set[Tuple[str, str]]:
    """Our markets (unordered pairs) a pending transaction could trade in: any
    pair of the assets it names. (Paths can route through XRP, so XRP is
    always included when a token of ours is named.)"""
    keys = {a.key for a in tx_assets(tx)}
    if not keys:
        return set()
    keys.add("XRP")
    return {m for m in markets if m[0] in keys and m[1] in keys}


class PaperBackrunner:
    def __init__(self, live: LiveFeed, db: DB, settings, network: str, approved: Set[str],
                 routes: Callable[[], List[Route]], urls: Optional[List[str]] = None):
        self.live = live
        self.db = db
        self.s = settings
        self.netname = f"{network}-backrun"
        self.approved = approved
        self.routes = routes
        self.urls = urls
        self.stats = {"pending_seen": 0, "triggers": 0, "priced": 0, "read_errors": 0, "chances": 0, "reconnects": 0,
                      "decide_ms_avg": 0}
        self._ws = None
        self._pending: Dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._stop = False
        self._logged: Set[Tuple[int, str]] = set()  # (target ledger, route) already logged
        self._busy: Set[Tuple[str, str]] = set()

    def stop(self) -> None:
        self._stop = True

    async def run(self) -> None:
        import websockets

        i, backoff = 0, 1.0
        while not self._stop:
            # same server as the live feed (it's the fastest that will carry us)
            urls = self.urls or ([self.live.server] if self.live.server else []) + self.live.urls
            url = urls[i % len(urls)]
            try:
                async with websockets.connect(url, max_size=None, ping_interval=20, open_timeout=15) as ws:
                    self._ws = ws
                    await ws.send(json.dumps({"id": 0, "command": "subscribe", "streams": ["transactions_proposed"]}))
                    backoff = 1.0
                    log.info("paper backrun: watching pending transactions on %s", url)
                    async for raw in ws:
                        m = json.loads(raw)
                        if m.get("type") == "response":
                            fut = self._pending.pop(m.get("id"), None)
                            if fut is not None and not fut.done():
                                fut.set_result(m)
                        elif m.get("type") == "transaction" and not m.get("validated"):
                            self._on_pending(m)
                        if self._stop:
                            return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("paper backrun %s dropped: %s %s", url, type(e).__name__, str(e)[:120])
            self._ws = None
            for f in self._pending.values():
                f.cancel()
            self._pending.clear()
            self.stats["reconnects"] += 1
            i += 1
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    async def _request(self, command: str, **params) -> dict:
        if self._ws is None:
            raise ConnectionError("not connected")
        self._next_id += 1
        fut = asyncio.get_running_loop().create_future()
        self._pending[self._next_id] = fut
        await self._ws.send(json.dumps({"id": self._next_id, "command": command, **params}))
        r = await asyncio.wait_for(fut, 3)
        if r.get("status") != "success":
            raise ConnectionError(f"{command}: {r.get('error')}")
        return r["result"]

    def _on_pending(self, m: dict) -> None:
        self.stats["pending_seen"] += 1
        tx = m.get("transaction") or m.get("tx_json") or {}
        if m.get("engine_result") != "tesSUCCESS" or tx.get("TransactionType") not in TRIGGER_TYPES:
            return  # only trades the open ledger actually applied can move prices
        if not self.live.healthy:
            return  # no trustworthy base prices to work from
        markets = touched_markets(tx, set(self.live.pairs))
        markets -= self._busy  # one look per market at a time
        if not markets:
            return
        self.stats["triggers"] += 1
        self._busy |= markets
        task = asyncio.create_task(self._evaluate(tx, int(m.get("ledger_current_index") or 0), markets, time.time()))
        task.add_done_callback(lambda _t, ms=markets: self._busy.difference_update(ms))

    async def _evaluate(self, tx: dict, target: int, markets: Set[Tuple[str, str]], seen_at: float) -> None:
        base = self.live.latest_snapshot
        if base is None:
            return
        edges = dict(base.edges)
        try:
            reads = []
            for key in markets:
                st = self.live.pairs[key]
                reads.append(self._read_current(st, tx.get("TransactionType") == "OfferCreate"))
            for st, amm, books in await asyncio.gather(*reads):
                for src, dst in ((st.a, st.b), (st.b, st.a)):
                    if (src.key, dst.key) not in edges:
                        continue  # the live feed left this market out (can't vouch for it)
                    rate = ONE if src.is_xrp else base.transfer_rates.get(src.issuer, ONE)
                    edges[(src.key, dst.key)] = Edge(
                        src=src, dst=dst,
                        offers=offers_to_edge_offers(books.get((dst.key, src.key), [])),
                        amm=amm_to_pool(amm, src) if amm else None,
                        transfer_rate_in=rate,
                    )
        except Exception as e:
            self.stats["read_errors"] += 1
            log.debug("paper backrun read failed: %s", e)
            return
        snap = replace(base, edges=edges)
        names = {k for m in markets for k in m} - {"XRP"}
        routes = [r for r in self.routes() if names & {t.key for t in r.tokens}]
        opps = scan(routes, snap, max_size=self.s.watch_max_size_xrp, min_size=self.s.min_trade_xrp,
                    min_profit=self.s.min_profit_xrp, slippage_buffer=self.s.slippage_buffer,
                    max_fee_drops=self.s.max_network_fee_drops, quick_reject=True)
        decided_ms = int((time.time() - seen_at) * 1000)
        n = self.stats["priced"] = self.stats["priced"] + 1
        self.stats["decide_ms_avg"] = int(self.stats["decide_ms_avg"] + (decided_ms - self.stats["decide_ms_avg"]) / n)
        since_close_ms = int((seen_at - self.live.closed_at) * 1000) if self.live.closed_at else None
        for o in opps:
            if not o.executable or (target, o.route.key) in self._logged:
                continue
            self._logged.add((target, o.route.key))
            self.stats["chances"] += 1
            self.db._insert("backrun_paper", dict(
                ts=now_iso(), network=self.netname, ledger_target=target, trigger_hash=tx.get("hash", ""),
                trigger_account=tx.get("Account", ""), trigger_type=tx.get("TransactionType", ""),
                route=o.route.key, hops=o.route.hops, size_xrp=o.size_xrp, safe_profit_xrp=o.safe_profit,
                would_trade=int(all(t.key in self.approved for t in o.route.tokens)),
                decided_ms=decided_ms, since_close_ms=since_close_ms,
            ))
        if len(self._logged) > 20000:
            self._logged = {x for x in self._logged if x[0] >= target - 10}

    async def _read_current(self, st, new_offer: bool = False):
        """A market's pool and (non-empty) books in the open ledger - i.e. with
        the pending trade already applied."""
        amm = None
        if st.amm is not None:
            r = await self._request("amm_info", asset=_issue_json(st.a), asset2=_issue_json(st.b), ledger_index="current")
            amm = r.get("amm")
        books = {}
        for gets, pays in ((st.b, st.a), (st.a, st.b)):
            bk = (gets.key, pays.key)
            if st.books.get(bk) or new_offer:  # an empty book can only fill up through a new offer
                r = await self._request("book_offers", taker_gets=_issue_json(gets), taker_pays=_issue_json(pays),
                                        limit=self.live.depth, ledger_index="current")
                books[bk] = r.get("offers", [])
        return st, amm, books
