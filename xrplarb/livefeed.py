"""Live markets: the servers push every change, we keep a copy in memory.

MarketFeed re-reads every order book and pool for every scan (~150 requests,
~13 s on public servers). LiveFeed instead subscribes, over one WebSocket, to
the ledger stream, to every AMM pool account and to every order book we care
about. When a ledger closes:

- pool balances are updated straight from the transactions' metadata (the
  ledger tells us each pool's new balances - no request needed);
- an order book that changed is re-read (one request per changed book);
- the snapshot is ready, usually well under a second after the close.

Safety nets, so a missed message can never leave us trading on wrong prices:
- every pair is also fully re-read in rotation, so no pair goes more than
  `full_every_s` seconds without a fresh read (well inside the 60 s rule);
- a fresh read of a pool at the same ledger we computed is compared with our
  copy; any difference is counted ("drift") and the read wins;
- if the connection drops or goes quiet, `next_snapshot` returns None and the
  caller falls back to the old full-read MarketFeed; after reconnecting
  everything is re-read before live snapshots resume.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Set, Tuple

from xrpl.models.requests import BookOffers, GenericRequest

from .amounts import ONE, XRP, Asset
from .market import MarketFeed, MarketSnapshot, amm_to_pool, offers_to_edge_offers
from .quote import Edge
from .xrplnet import Net, _cur

log = logging.getLogger(__name__)

ACCOUNT_ZERO = "rrrrrrrrrrrrrrrrrrrrrhoLvTp"  # 'taker' for book subscriptions: nobody in particular
DROPS = Decimal(1_000_000)
SUB_CHUNK = 8  # books / accounts per subscribe message (big bursts get "tooBusy")
Pair = Tuple[str, str]  # (a.key, b.key) with a.key <= b.key


def _norm(a: Asset, b: Asset) -> Tuple[Asset, Asset]:
    return (a, b) if a.key <= b.key else (b, a)


def _asset_of(amount) -> Asset:
    """Asset of a ledger amount (drops string = XRP, else {currency, issuer, value})."""
    if isinstance(amount, (str, int)):
        return XRP
    return Asset(amount["currency"], amount["issuer"])


def _issue_json(a: Asset) -> dict:
    return {"currency": "XRP"} if a.is_xrp else {"currency": a.currency, "issuer": a.issuer}


@dataclass
class PairState:
    a: Asset
    b: Asset
    amm: Optional[dict] = None          # raw amm_info "amm" object (balances kept current)
    amm_known: bool = False             # True once amm_info has answered (even "no pool")
    amm_at: Tuple[int, int] = (0, 0)    # (ledger, tx index) our pool copy is current to
    books: Dict[Tuple[str, str], List[dict]] = field(default_factory=dict)  # (gets, pays) -> raw offers
    books_at: Dict[Tuple[str, str], int] = field(default_factory=dict)       # (gets, pays) -> ledger read
    dirty_books: Dict[Tuple[str, str], int] = field(default_factory=dict)    # (gets, pays) -> ledger changed
    amm_dirty: int = 0                  # ledger at which the pool needs a full re-read (0 = no)
    full_read_at: float = 0.0           # wall time of the last full re-read

    @property
    def key(self) -> Pair:
        return (self.a.key, self.b.key)

    def synced(self) -> bool:
        return self.amm_known and len(self.books_at) == 2


def pool_changes(meta: dict, pools: Dict[str, Pair]) -> Tuple[Dict[str, Dict[str, object]], Set[str]]:
    """From one transaction's metadata: new balances for the pools it touched,
    and pools that need a full re-read (pool deleted, fee/auction change,
    trust line flags changed e.g. a freeze).

    Returns ({pool account: {asset key: new amount in amm_info format}}, {pool accounts to re-read})."""
    changes: Dict[str, Dict[str, object]] = {}
    reread: Set[str] = set()
    for wrapper in meta.get("AffectedNodes", []):
        kind, node = next(iter(wrapper.items()))
        typ = node.get("LedgerEntryType")
        fields = node.get("FinalFields") or node.get("NewFields") or {}
        if typ == "AccountRoot":
            acct = fields.get("Account")
            if acct in pools:
                if kind == "DeletedNode" or "Balance" not in fields:
                    reread.add(acct)
                else:
                    changes.setdefault(acct, {})["XRP"] = str(fields["Balance"])
        elif typ == "RippleState":
            low = (fields.get("LowLimit") or {}).get("issuer")
            high = (fields.get("HighLimit") or {}).get("issuer")
            bal = fields.get("Balance") or {}
            for pool, issuer, sign in ((low, high, 1), (high, low, -1)):
                if pool not in pools:
                    continue
                prev = node.get("PreviousFields") or {}
                if kind == "DeletedNode" or "Flags" in prev or not bal:
                    reread.add(pool)
                    continue
                # Balance is from the low account's side: positive = low holds high's tokens.
                value = Decimal(str(bal["value"])) * sign
                key = Asset(bal["currency"], issuer).key
                changes.setdefault(pool, {})[key] = {"currency": bal["currency"], "issuer": issuer, "value": str(value)}
        elif typ == "AMM":
            acct = fields.get("Account")
            if acct in pools:
                reread.add(acct)
    return changes, reread


def book_changes(meta: dict) -> Set[Tuple[str, str]]:
    """(gets key, pays key) of every order book an Offer in this transaction belongs to."""
    out: Set[Tuple[str, str]] = set()
    for wrapper in meta.get("AffectedNodes", []):
        _, node = next(iter(wrapper.items()))
        if node.get("LedgerEntryType") != "Offer":
            continue
        f = node.get("FinalFields") or node.get("NewFields") or {}
        if "TakerGets" in f and "TakerPays" in f:
            out.add((_asset_of(f["TakerGets"]).key, _asset_of(f["TakerPays"]).key))
    return out


def same_amount(x, y) -> bool:
    """Compare two ledger amounts by value ("1E-7" and "0.0000001" are equal)."""
    if isinstance(x, dict) and isinstance(y, dict):
        return (x.get("currency"), x.get("issuer")) == (y.get("currency"), y.get("issuer")) and \
            Decimal(str(x.get("value"))) == Decimal(str(y.get("value")))
    return isinstance(x, (str, int)) and isinstance(y, (str, int)) and int(x) == int(y)


def apply_pool_change(amm: dict, new: Dict[str, object]) -> None:
    """Write new balances into an amm_info object, matching each side by asset."""
    for side in ("amount", "amount2"):
        k = _asset_of(amm[side]).key
        if k in new:
            amm[side] = new[k]


async def rank_servers(urls: List[str], seconds: float = 20.0) -> List[Tuple[str, Optional[float]]]:
    """Listen to the ledger stream on every server at once and rank them by how
    soon each tells us a ledger has closed (median delay behind whichever
    server reported that ledger first). What counts is this delay - not
    distance or ping - because it is when our scan can start.

    Returns [(url, median delay in seconds or None if it didn't keep up)], fastest first."""
    import websockets

    first: Dict[int, float] = {}
    seen: Dict[str, Dict[int, float]] = {u: {} for u in urls}

    async def listen(u: str) -> None:
        try:
            async with websockets.connect(u, open_timeout=10, max_size=None) as ws:
                await ws.send(json.dumps({"id": 1, "command": "subscribe", "streams": ["ledger"]}))
                async for raw in ws:
                    m = json.loads(raw)
                    if m.get("type") == "ledgerClosed":
                        t, li = time.time(), int(m["ledger_index"])
                        seen[u][li] = t
                        first[li] = min(first.get(li, t), t)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.info("server check: %s didn't answer (%s)", u, type(e).__name__)

    tasks = [asyncio.create_task(listen(u)) for u in urls]
    await asyncio.sleep(seconds)
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    ranked: List[Tuple[str, Optional[float]]] = []
    for u in urls:
        lags = sorted(seen[u][li] - first[li] for li in first if li in seen[u])
        # a server that missed more than a third of the ledgers isn't keeping up
        ok = first and len(lags) >= max(1, len(first) * 2 // 3)
        ranked.append((u, lags[len(lags) // 2] if ok else None))
    ranked.sort(key=lambda x: (x[1] is None, x[1] or 0.0))
    return ranked


class LiveFeed:
    def __init__(
        self,
        net: Net,
        ws_urls: List[str],
        depth: int = 30,
        our_accounts: Iterable[str] = (),
        full_every_s: float = 40.0,
        quiet_s: float = 0.15,
        max_settle_s: float = 1.0,
    ):
        self.net = net
        self.urls = list(ws_urls)
        self.depth = depth
        self.our_accounts = list(our_accounts)
        self.full_every_s = full_every_s
        self.quiet_s = quiet_s
        self.max_settle_s = max_settle_s
        self.rates = MarketFeed(net, depth, our_accounts)  # transfer-rate cache + fallback
        self.pairs: Dict[Pair, PairState] = {}
        self.pool_of: Dict[str, Pair] = {}          # pool account -> pair
        self.book_pair: Dict[Tuple[str, str], Pair] = {}  # (gets, pays) -> pair
        self.connected = False
        self.synced = False
        self.ledger: Optional[int] = None
        self.closed_at = 0.0
        self.fees = {"base_fee_drops": 10, "reserve_base_xrp": ONE, "reserve_inc_xrp": Decimal("0.2")}
        self._load = (0.0, ONE)
        self._rates: Dict[str, Tuple[float, Decimal]] = {}
        self._bg: Set[asyncio.Task] = set()
        self._rotating = False
        self._last_msg = 0.0
        self._ws = None
        self._ready: asyncio.Queue = asyncio.Queue(maxsize=1)
        self._stop = False
        self._subscribed: Set[str] = set()
        self._pending: Dict[int, asyncio.Future] = {}
        self.timing = (0, 0)  # last ledger: (ms waiting for its transactions, ms re-reading changed books)
        self.ready_ms: List[int] = []
        self.latest_snapshot: Optional[MarketSnapshot] = None  # newest snapshot (also used by the backrunner)
        self.server_delays: List[Tuple[str, Optional[float]]] = []  # last ranking, fastest first
        self.server = ""  # the server we're connected to
        self.rank_every_s = 6 * 3600
        self._ranked_at = 0.0
        self._refused: Dict[str, float] = {}  # server -> when it refused our subscriptions  # recent "snapshot ready N ms after the ledger closed"
        self._next_id = 0
        self.stats = {"ledgers": 0, "pool_updates": 0, "book_reads": 0, "full_reads": 0, "drift": 0,
                      "reconnects": 0, "late_tx": 0}

    # ---- configuration ---------------------------------------------------------
    def configure(self, tokens: Iterable[Asset], token_pairs: Iterable[Tuple[Asset, Asset]] = ()) -> None:
        wanted = {_norm(XRP, t) for t in tokens}
        wanted |= {_norm(a, b) for a, b in token_pairs if a != b}
        keep = {}
        for a, b in wanted:
            st = self.pairs.get((a.key, b.key)) or PairState(a, b)
            keep[st.key] = st
            self.book_pair[(b.key, a.key)] = st.key
            self.book_pair[(a.key, b.key)] = st.key
        self.pairs = keep

    @property
    def healthy(self) -> bool:
        return self.connected and self.synced and time.time() - self._last_msg < 15

    # ---- connection ------------------------------------------------------------
    async def run(self) -> None:
        import websockets

        i, backoff = 0, 1.0
        while not self._stop:
            if len(self.urls) > 1 and time.time() - self._ranked_at > min(self.rank_every_s, 1800 if i else self.rank_every_s):
                # use whichever server tells us about new ledgers first (re-checked
                # at start, every few hours, and after a drop if the last check is old)
                await self._rank()
                i = 0
            url = self.urls[i % len(self.urls)]
            try:
                async with websockets.connect(url, max_size=None, ping_interval=20, open_timeout=15) as ws:
                    self._ws, self._subscribed = ws, set()
                    self.synced = False
                    for st in self.pairs.values():  # whatever happened while we were away: re-read it all
                        st.full_read_at = 0.0
                    reader = asyncio.create_task(self._read(ws))
                    worker = None
                    try:
                        await self._subscribe(streams=["ledger"])
                        books = [
                            {"taker_gets": _issue_json(st.a), "taker_pays": _issue_json(st.b), "taker": ACCOUNT_ZERO, "both": True}
                            for st in self.pairs.values()
                        ]
                        for i in range(0, len(books), SUB_CHUNK):
                            await self._subscribe(books=books[i:i + SUB_CHUNK])
                        await self._subscribe_pools()
                        self.connected, backoff, self.server = True, 1.0, url
                        log.info("live feed connected: %s (%d markets)", url, len(self.pairs))
                        worker = asyncio.create_task(self._ledger_worker())
                        await self._until_rerank(reader)
                    finally:
                        reader.cancel()
                        if worker:
                            worker.cancel()
                        for f in self._pending.values():
                            f.cancel()
                        self._pending.clear()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("live feed %s dropped: %s %s", url, type(e).__name__, str(e)[:120])
                if "too busy" in str(e) and len(self.urls) > 1:
                    # this server won't carry all our markets (some cap how many
                    # books one connection may follow): use the next fastest
                    # until the next speed check
                    self.urls = [u for u in self.urls if u != url] + [url]
                    self._refused[url] = time.time()
                    i = -1
            self.connected = self.synced = False
            self._ws = None
            self.stats["reconnects"] += 1
            i += 1
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    def _order(self, ranked: List[Tuple[str, Optional[float]]]) -> List[Tuple[str, Optional[float]]]:
        """Fastest first, but a server that refused our subscriptions in the
        last few hours goes to the back (it would only refuse again)."""
        fresh = lambda u: time.time() - self._refused.get(u, 0) < self.rank_every_s  # noqa: E731
        return sorted(ranked, key=lambda x: (fresh(x[0]), x[1] is None, x[1] or 0.0))

    async def _rank(self) -> None:
        ranked = self._order(await rank_servers(self.urls))
        self._ranked_at = time.time()
        self.server_delays = ranked
        if ranked and ranked[0][1] is not None:
            self.urls = [u for u, _ in ranked]
        log.info("live feed: servers by speed: %s", ", ".join(
            f"{u} {'+%d ms' % (d * 1000) if d is not None else 'not keeping up'}" for u, d in ranked))

    async def _until_rerank(self, reader: asyncio.Task) -> None:
        """Stay on this server until the connection ends, or until it's time to
        check again whether another server has become faster."""
        while not reader.done():
            await asyncio.wait({reader}, timeout=60)
            if len(self.urls) > 1 and time.time() - self._ranked_at > self.rank_every_s:
                ranked = self._order(await rank_servers(self.urls))
                self._ranked_at, self.server_delays = time.time(), ranked
                mine = dict(ranked).get(self.server)
                best_url, best = ranked[0]
                # switch only for a clear win (a reconnect costs a full re-read)
                if best is not None and best_url != self.server and (mine is None or mine - best > 0.15):
                    log.info("live feed: %s is now faster than %s, switching", best_url, self.server)
                    self.urls = [u for u, _ in ranked]
                    reader.cancel()
                    raise ConnectionError("switching to a faster server")
        await reader

    def stop(self) -> None:
        self._stop = True

    async def _read(self, ws) -> None:
        async for raw in ws:
            self._on_message(json.loads(raw))
            if self._stop:
                return

    async def _ws_request(self, command: str, timeout: float = 5.0, **params) -> dict:
        """One request over the live connection (no new connection, no HTTP
        overhead): used for the reads a fresh ledger is waiting on."""
        if self._ws is None:
            raise ConnectionError("not connected")
        self._next_id += 1
        fut = asyncio.get_running_loop().create_future()
        self._pending[self._next_id] = fut
        await self._ws.send(json.dumps({"id": self._next_id, "command": command, **params}))
        r = await asyncio.wait_for(fut, timeout)
        if r.get("status") != "success":
            raise ConnectionError(f"{command}: {r.get('error')}")
        return r["result"]

    async def _subscribe(self, **params) -> None:
        """Subscribe and wait for the answer. Public servers sometimes say
        "tooBusy" to a burst of subscriptions: back off and ask again; if one
        keeps refusing, give up on it (the caller moves to the next server)."""
        for attempt in range(4):
            if self._ws is None:
                raise ConnectionError("not connected")
            self._next_id += 1
            fut = asyncio.get_running_loop().create_future()
            self._pending[self._next_id] = fut
            await self._ws.send(json.dumps({"id": self._next_id, "command": "subscribe", **params}))
            r = await asyncio.wait_for(fut, 20)
            if r.get("status") == "success":
                return
            if r.get("error") not in ("tooBusy", "slowDown"):
                raise ConnectionError(f"subscribe refused: {r.get('error')}")
            await asyncio.sleep(2 ** attempt)
        raise ConnectionError("subscribe refused: server stays too busy")

    async def _subscribe_pools(self) -> None:
        new = [p for p in self.pool_of if p not in self._subscribed]
        for i in range(0, len(new), SUB_CHUNK):
            await self._subscribe(accounts=new[i:i + SUB_CHUNK])
            self._subscribed.update(new[i:i + SUB_CHUNK])

    # ---- incoming messages -----------------------------------------------------
    def _on_message(self, m: dict) -> None:
        self._last_msg = time.time()
        typ = m.get("type")
        if typ == "ledgerClosed":
            self.ledger = int(m["ledger_index"])
            self.closed_at = time.time()
            self.fees = {
                "base_fee_drops": int(m.get("fee_base", 10)),
                "reserve_base_xrp": Decimal(int(m.get("reserve_base", 1_000_000))) / DROPS,
                "reserve_inc_xrp": Decimal(int(m.get("reserve_inc", 200_000))) / DROPS,
            }
            self._pending_close = True
        elif typ == "transaction" and m.get("validated"):
            self._on_tx(m)
        elif typ == "response":
            fut = self._pending.pop(m.get("id"), None)
            if fut is not None and not fut.done():
                fut.set_result(m)

    def _on_tx(self, m: dict) -> None:
        meta = m.get("meta") or {}
        if meta.get("TransactionResult") != "tesSUCCESS" and not str(meta.get("TransactionResult", "")).startswith("tec"):
            return
        at = (int(m.get("ledger_index") or 0), int(meta.get("TransactionIndex", 0)))
        if self.ledger and at[0] < self.ledger:
            self.stats["late_tx"] += 1
        changes, reread = pool_changes(meta, self.pool_of)
        for pool, new in changes.items():
            st = self.pairs.get(self.pool_of[pool])
            if st is None or st.amm is None or at <= st.amm_at:
                continue  # our copy already includes this transaction
            apply_pool_change(st.amm, new)
            st.amm_at = at
            self.stats["pool_updates"] += 1
        for pool in reread:
            st = self.pairs.get(self.pool_of[pool])
            if st is not None:
                st.amm_dirty = max(st.amm_dirty, at[0])
        for bk in book_changes(meta):
            key = self.book_pair.get(bk)
            if key and key in self.pairs:
                st = self.pairs[key]
                if st.books_at.get(bk, 0) < at[0]:
                    st.dirty_books[bk] = max(st.dirty_books.get(bk, 0), at[0])

    # ---- per-ledger work -------------------------------------------------------
    _pending_close = False

    async def _ledger_worker(self) -> None:
        while True:
            await asyncio.sleep(0.02)
            if not self._pending_close:
                continue
            # A ledger's transactions arrive just after (sometimes just before)
            # its "closed" message: wait for the burst to finish.
            start = self.closed_at
            while time.time() - start < self.max_settle_s and time.time() - self._last_msg < self.quiet_s:
                await asyncio.sleep(0.02)
            self._pending_close = False
            ledger, closed_at = self.ledger, self.closed_at
            settled = time.time()
            try:
                await self._refresh_changed()
                self.timing = (int((settled - closed_at) * 1000), int((time.time() - settled) * 1000))
                if not self.synced:
                    await asyncio.gather(self._full_reads(list(self.pairs.values())), self._refresh_rates(),
                                         self._refresh_load())
                    # pairs that failed to read are simply left out of snapshots
                    # (see _build) until the rotation reads them
                    self.synced = True
                    continue
                snap = self._build(ledger, closed_at)
                self.stats["ledgers"] += 1
                if self._ready.full():
                    self._ready.get_nowait()  # nobody took the last one: replace it with the newer
                self._ready.put_nowait(snap)
                self.latest_snapshot = snap
                self.ready_ms = (self.ready_ms + [int((time.time() - closed_at) * 1000)])[-500:]
                self._background()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("live feed ledger %s: %s %s", ledger, type(e).__name__, str(e)[:160])

    def _background(self) -> None:
        """Slow upkeep runs beside the next ledger, never in front of it."""
        def spawn(coro):
            t = asyncio.create_task(coro)
            self._bg.add(t)
            t.add_done_callback(self._bg.discard)

        if not self._rotating:
            spawn(self._rotate())
        now = time.time()
        if now - self._load[0] > 30:
            self._load = (now, self._load[1])
            spawn(self._refresh_load())
        if self._rates and now - min(t for t, _ in self._rates.values()) > 600:
            for iss in self._rates:
                self._rates[iss] = (now, self._rates[iss][1])
            spawn(self._refresh_rates())

    async def _rotate(self) -> None:
        """Re-read the stalest pairs, so every pair is fully re-read at least
        every `full_every_s` seconds whatever the stream says."""
        self._rotating = True
        try:
            n = max(1, math.ceil(len(self.pairs) * 3.9 / self.full_every_s))
            stale = sorted(self.pairs.values(), key=lambda s: s.full_read_at)[:n]
            await self._full_reads([s for s in stale if time.time() - s.full_read_at > self.full_every_s / 2])
        finally:
            self._rotating = False

    async def _refresh_load(self) -> None:
        try:
            self._load = (time.time(), (await self.net.server_state())["load_factor"])
        except Exception as e:
            log.debug("live feed load factor: %s", e)

    async def _refresh_rates(self) -> None:
        issuers = {x.issuer for st in self.pairs.values() for x in (st.a, st.b) if not x.is_xrp}
        self.rates._rate_cache.clear()
        res = await asyncio.gather(*(self.rates.transfer_rate(i) for i in issuers), return_exceptions=True)
        for iss, r in zip(issuers, res):
            if isinstance(r, Decimal):
                self._rates[iss] = (time.time(), r)

    async def _read_book(self, st: PairState, gets: Asset, pays: Asset, urgent: bool = False) -> None:
        r = None
        if urgent:
            try:
                r = await self._ws_request("book_offers", taker_gets=_issue_json(gets), taker_pays=_issue_json(pays),
                                           limit=self.depth, ledger_index="validated")
            except Exception as e:
                log.debug("live feed: book over the live connection failed (%s), using the request servers", e)
        if r is None:
            r = await self.net.request(BookOffers(taker_gets=_cur(gets), taker_pays=_cur(pays), limit=self.depth,
                                                  ledger_index="validated"))
        bk = (gets.key, pays.key)
        led = int(r.get("ledger_index") or 0)
        st.books[bk] = r.get("offers", [])
        st.books_at[bk] = led
        if st.dirty_books.get(bk, 0) <= led:
            st.dirty_books.pop(bk, None)
        self.stats["book_reads"] += 1

    async def _read_amm(self, st: PairState) -> None:
        try:
            r = await self.net.request(GenericRequest(method="amm_info", asset=_cur(st.a).to_dict(),
                                                      asset2=_cur(st.b).to_dict(), ledger_index="validated"))
        except Exception as e:
            if getattr(e, "error", None) in ("actNotFound", "entryNotFound", "invalidParams"):
                st.amm, st.amm_known, st.amm_dirty = None, True, 0
                return
            raise
        amm, led = r.get("amm"), int(r.get("ledger_index") or 0)
        if amm and st.amm is not None and led == st.amm_at[0] and st.amm_at[1] < 10**9:
            # we computed this ledger's balances from the stream: check them
            for side in ("amount", "amount2"):
                if not same_amount(st.amm.get(side), amm.get(side)):
                    self.stats["drift"] += 1
                    log.warning("live feed drift on %s/%s at ledger %s: had %s, ledger says %s",
                                st.a.short, st.b.short, led, st.amm.get(side), amm.get(side))
                    break
        if amm is None or led >= st.amm_at[0]:
            st.amm, st.amm_at = amm, (led, 10**9)  # 10**9: includes every transaction of that ledger
        st.amm_known = True
        if st.amm_dirty <= led:
            st.amm_dirty = 0
        if amm and amm.get("account") and amm["account"] not in self.pool_of:
            self.pool_of[amm["account"]] = st.key  # subscribed after this round of reads

    async def _full_reads(self, states: List[PairState]) -> None:
        async def one(st: PairState):
            await asyncio.gather(self._read_amm(st), self._read_book(st, st.b, st.a), self._read_book(st, st.a, st.b))
            st.full_read_at = time.time()
            self.stats["full_reads"] += 1

        res = await asyncio.gather(*(one(st) for st in states), return_exceptions=True)
        for r in res:
            if isinstance(r, Exception):
                log.debug("live feed full read failed: %s", r)
        # pools found by these reads: start following them. A pool's copy is
        # current to the ledger it was read at; anything later arrives by stream.
        try:
            await self._subscribe_pools()
        except Exception as e:
            log.warning("live feed: couldn't follow new pools yet: %s", e)

    async def _refresh_changed(self) -> None:
        jobs = []
        for st in self.pairs.values():
            if st.amm_dirty:
                jobs.append(self._read_amm(st))
            for gets_key, pays_key in list(st.dirty_books):
                gets = st.a if st.a.key == gets_key else st.b
                pays = st.b if gets is st.a else st.a
                jobs.append(self._read_book(st, gets, pays, urgent=True))
        if jobs:
            for r in await asyncio.gather(*jobs, return_exceptions=True):
                if isinstance(r, Exception):
                    log.debug("live feed re-read failed: %s", r)

    def _build(self, ledger: Optional[int], closed_at: float) -> MarketSnapshot:
        snap = MarketSnapshot(ledger_index=ledger, taken_at=closed_at, load_factor=self._load[1], **self.fees)
        snap.transfer_rates = {iss: r for iss, (_, r) in self._rates.items()}
        lim = ledger or 0
        for st in self.pairs.values():
            # A pair we couldn't keep current is left out rather than priced on
            # old data: a pending re-read, a failed read, or no full read for 60 s.
            # So is a token whose issuer fee we don't know (assuming none could
            # invent a profit).
            stale = (
                (st.amm_dirty and st.amm_dirty <= lim)
                or any(v <= lim for v in st.dirty_books.values())
                or not st.synced()
                or time.time() - st.full_read_at > 60
                or any(not x.is_xrp and x.issuer not in self._rates for x in (st.a, st.b))
            )
            for src, dst in ((st.a, st.b), (st.b, st.a)):
                if stale:
                    snap.fetch_errors += 1
                    continue
                rate = ONE if src.is_xrp else snap.transfer_rates[src.issuer]
                snap.edges[(src.key, dst.key)] = Edge(
                    src=src, dst=dst,
                    offers=offers_to_edge_offers(st.books.get((dst.key, src.key), [])),
                    amm=amm_to_pool(st.amm, src, self.our_accounts) if st.amm else None,
                    transfer_rate_in=rate,
                )
        return snap

    async def next_snapshot(self, timeout: float = 8.0) -> Optional[MarketSnapshot]:
        """The snapshot for the next ledger, or None if the live feed isn't
        healthy (caller should fall back to a full MarketFeed read)."""
        # not healthy (starting, reconnecting): wait a little in case it recovers
        wait = timeout if self.healthy else min(timeout, 1.0)
        try:
            return await asyncio.wait_for(self._ready.get(), wait)
        except asyncio.TimeoutError:
            return None
