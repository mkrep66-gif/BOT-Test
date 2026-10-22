"""Reads the live markets for a set of tokens and turns them into Edges.

One MarketSnapshot = a picture of every relevant order book + AMM pool at one
validated ledger. Bots evaluate their routes against the latest snapshot.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .amounts import ONE, XRP, ZERO, Asset, parse_amount
from .quote import AmmPool, Edge, Offer
from .xrplnet import Net

log = logging.getLogger(__name__)

TRANSFER_RATE_UNIT = Decimal(1_000_000_000)


def offers_to_edge_offers(raw_offers: List[dict], exclude_owner: Optional[str] = None) -> List[Offer]:
    """Convert book_offers results (owners give X, want Y) into taker-side Offers."""
    out: List[Offer] = []
    for o in raw_offers:
        if exclude_owner and o.get("Account") == exclude_owner:
            continue
        gets = o.get("taker_gets_funded", o["TakerGets"])
        pays = o.get("taker_pays_funded", o["TakerPays"])
        _, g = parse_amount(gets)
        _, p = parse_amount(pays)
        if g <= 0 or p <= 0:
            continue
        out.append(Offer(out_amount=g, in_amount=p, owner=o.get("Account", "")))
    out.sort(key=lambda x: x.price)
    return out


def amm_to_pool(amm: dict, src: Asset) -> Optional[AmmPool]:
    a1, v1 = parse_amount(amm["amount"])
    a2, v2 = parse_amount(amm["amount2"])
    if amm.get("asset_frozen") or amm.get("asset2_frozen"):
        return None
    fee = Decimal(int(amm.get("trading_fee", 0))) / Decimal(100_000)
    if a1 == src:
        return AmmPool(pool_in=v1, pool_out=v2, fee=fee, account=amm.get("account", ""))
    if a2 == src:
        return AmmPool(pool_in=v2, pool_out=v1, fee=fee, account=amm.get("account", ""))
    return None


@dataclass
class MarketSnapshot:
    ledger_index: Optional[int]
    taken_at: float
    edges: Dict[Tuple[str, str], Edge] = field(default_factory=dict)
    transfer_rates: Dict[str, Decimal] = field(default_factory=dict)  # issuer -> rate (>=1)
    base_fee_drops: int = 10
    load_factor: Decimal = ONE
    reserve_base_xrp: Decimal = Decimal(1)
    reserve_inc_xrp: Decimal = Decimal("0.2")
    fetch_errors: int = 0

    def edge(self, src: Asset, dst: Asset) -> Optional[Edge]:
        return self.edges.get((src.key, dst.key))

    def pairs(self) -> List[Tuple[Asset, Asset]]:
        return [(e.src, e.dst) for e in self.edges.values() if e.has_liquidity()]

    def age(self) -> float:
        return time.time() - self.taken_at


class MarketFeed:
    """Fetches books + AMMs for the configured pairs."""

    def __init__(self, net: Net, depth: int = 30):
        self.net = net
        self.depth = depth
        self._rate_cache: Dict[str, Tuple[float, Decimal]] = {}
        self.latest: Optional[MarketSnapshot] = None

    async def transfer_rate(self, issuer: str) -> Decimal:
        hit = self._rate_cache.get(issuer)
        if hit and time.time() - hit[0] < 600:
            return hit[1]
        info = await self.net.account_info(issuer)
        rate = ONE
        if info and info.get("TransferRate"):
            rate = Decimal(int(info["TransferRate"])) / TRANSFER_RATE_UNIT
        self._rate_cache[issuer] = (time.time(), rate)
        return rate

    async def snapshot(
        self,
        tokens: Iterable[Asset],
        token_pairs: Iterable[Tuple[Asset, Asset]] = (),
        exclude_owner: Optional[str] = None,
    ) -> MarketSnapshot:
        """tokens: every token paired against XRP. token_pairs: extra token/token
        markets (unordered) to include."""
        tokens = list(tokens)
        def norm(a: Asset, b: Asset) -> Tuple[Asset, Asset]:
            return (a, b) if a.key <= b.key else (b, a)

        unordered: Set[Tuple[Asset, Asset]] = {norm(XRP, t) for t in tokens}
        for a, b in token_pairs:
            if a != b:
                unordered.add(norm(a, b))

        state = await self.net.server_state()
        snap = MarketSnapshot(
            ledger_index=state["ledger_index"],
            taken_at=time.time(),
            base_fee_drops=state["base_fee_drops"],
            load_factor=state["load_factor"],
            reserve_base_xrp=state["reserve_base_xrp"],
            reserve_inc_xrp=state["reserve_inc_xrp"],
        )

        issuers = {a.issuer for pair in unordered for a in pair if not a.is_xrp}
        rates = await asyncio.gather(*(self.transfer_rate(i) for i in issuers), return_exceptions=True)
        for iss, r in zip(issuers, rates):
            snap.transfer_rates[iss] = r if isinstance(r, Decimal) else ONE
            if not isinstance(r, Decimal):
                snap.fetch_errors += 1

        async def fetch_pair(a: Asset, b: Asset):
            res = await asyncio.gather(
                self.net.book_offers(gets=b, pays=a, limit=self.depth),  # we give a, get b
                self.net.book_offers(gets=a, pays=b, limit=self.depth),  # we give b, get a
                self.net.amm_info(a, b),
                return_exceptions=True,
            )
            return a, b, res

        results = await asyncio.gather(*(fetch_pair(a, b) for a, b in unordered))
        for a, b, (ab, ba, amm) in results:
            for src, dst, raw in ((a, b, ab), (b, a, ba)):
                if isinstance(raw, Exception):
                    snap.fetch_errors += 1
                    raw = []
                pool = None
                if amm and not isinstance(amm, Exception):
                    pool = amm_to_pool(amm, src)
                elif isinstance(amm, Exception):
                    snap.fetch_errors += 1
                rate = ONE if src.is_xrp else snap.transfer_rates.get(src.issuer, ONE)
                snap.edges[(src.key, dst.key)] = Edge(
                    src=src,
                    dst=dst,
                    offers=offers_to_edge_offers(raw, exclude_owner),
                    amm=pool,
                    transfer_rate_in=rate,
                )
        self.latest = snap
        return snap


def liquidation_value(snap: MarketSnapshot, holdings: Dict[Asset, Decimal]) -> Dict[Asset, Decimal]:
    """What each token holding would *actually* sell for in XRP right now
    (walking the book/AMM, including fees) - not its face value."""
    from .quote import swap

    out: Dict[Asset, Decimal] = {}
    for asset, amt in holdings.items():
        if asset.is_xrp or amt <= 0:
            continue
        e = snap.edge(asset, XRP)
        if e is None or not e.has_liquidity():
            out[asset] = ZERO
            continue
        out[asset] = swap(e, amt).amount_out
    return out
