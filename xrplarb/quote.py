"""Pure price math: how much do I get out of one market (edge) for a given input?

An *edge* is one direction of one market, e.g. "give XRP, get USD.rXYZ".
Liquidity on an edge can come from two places, and the XRPL payment engine uses
whichever is cheaper at each moment:

  * the order book (CLOB): a list of standing offers, best price first
  * an AMM pool: a constant-product pool with a trading fee

We also charge the token issuer's *transfer fee* on what we send in, because
on the XRPL the sender pays it whenever a token moves between two
non-issuer accounts.

Everything here is deliberately conservative: when unsure, we assume we get
slightly less, never more.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional, Sequence

from .amounts import ONE, ZERO, Asset


@dataclass
class Offer:
    """A standing offer, seen from the taker's (our) side.

    out_amount: how much of the edge's OUTPUT asset the offer can give us (funded)
    in_amount:  how much of the edge's INPUT asset it wants for all of that
    """

    out_amount: Decimal
    in_amount: Decimal
    owner: str = ""

    @property
    def price(self) -> Decimal:
        """Input needed per 1 unit of output (lower is better for us)."""
        return self.in_amount / self.out_amount


@dataclass
class AmmPool:
    """Constant-product pool. pool_in/pool_out are the reserves of the edge's
    input and output assets; fee is a fraction (0.003 = 0.3%)."""

    pool_in: Decimal
    pool_out: Decimal
    fee: Decimal
    account: str = ""

    def out_for(self, amount_in: Decimal) -> Decimal:
        if amount_in <= 0:
            return ZERO
        eff = amount_in * (ONE - self.fee)
        return self.pool_out * eff / (self.pool_in + eff)

    def spot_rate(self) -> Decimal:
        """Output per unit input for an infinitesimal trade (after fee)."""
        return self.pool_out / self.pool_in * (ONE - self.fee)


@dataclass
class Edge:
    src: Asset
    dst: Asset
    offers: List[Offer] = field(default_factory=list)  # sorted best (lowest price) first
    amm: Optional[AmmPool] = None
    transfer_rate_in: Decimal = ONE  # e.g. 1.002 for a 0.2% issuer transfer fee on src

    @property
    def key(self) -> tuple[str, str]:
        return (self.src.key, self.dst.key)

    def has_liquidity(self) -> bool:
        return bool(self.offers) or (self.amm is not None and self.amm.pool_out > 0)

    def marginal_rate(self) -> Decimal:
        """Best output per unit input for a tiny trade, after fees. 0 if no liquidity."""
        best = ZERO
        if self.offers:
            best = ONE / self.offers[0].price
        if self.amm is not None and self.amm.pool_in > 0:
            best = max(best, self.amm.spot_rate())
        return best / self.transfer_rate_in


@dataclass
class SwapResult:
    amount_in: Decimal
    amount_out: Decimal
    transfer_fee_paid: Decimal
    amm_fee_paid: Decimal
    from_book: Decimal
    from_amm: Decimal


def _walk_book(offers: Sequence[Offer], start_idx: int, start_used_in: Decimal, amount_in: Decimal):
    """Consume `amount_in` from the book starting at offer start_idx (with
    start_used_in of that offer already taken). Returns (out, idx, used_in, consumed_in)."""
    out = ZERO
    remaining = amount_in
    idx = start_idx
    used = start_used_in
    while remaining > 0 and idx < len(offers):
        o = offers[idx]
        avail_in = o.in_amount - used
        if avail_in <= 0:
            idx += 1
            used = ZERO
            continue
        take = min(remaining, avail_in)
        out += take / o.price
        remaining -= take
        used += take
        if used >= o.in_amount:
            idx += 1
            used = ZERO
    return out, idx, used, amount_in - remaining


def swap(edge: Edge, amount_in: Decimal, chunks: int = 40) -> SwapResult:
    """Simulate sending `amount_in` of edge.src through the edge.

    If both a book and an AMM exist we split the trade into chunks and, chunk by
    chunk, use whichever source pays more - which is what the ledger's payment
    engine does (it compares AMM and book quality step by step).
    """
    if amount_in <= 0:
        return SwapResult(amount_in, ZERO, ZERO, ZERO, ZERO, ZERO)

    eff_in = amount_in / edge.transfer_rate_in
    transfer_fee = amount_in - eff_in

    offers = edge.offers
    amm = edge.amm

    if amm is None:
        # If the book is too thin this is a partial output; quote_loop flags
        # that case via max_input().
        out, _, _, _ = _walk_book(offers, 0, ZERO, eff_in)
        return SwapResult(amount_in, out, transfer_fee, ZERO, out, ZERO)

    if not offers:
        out = amm.out_for(eff_in)
        return SwapResult(amount_in, out, transfer_fee, eff_in * amm.fee, ZERO, out)

    # Mixed: chunked greedy.
    x, y = amm.pool_in, amm.pool_out
    idx, used = 0, ZERO
    chunk = eff_in / chunks
    total_out = ZERO
    from_book = ZERO
    from_amm = ZERO
    amm_fee = ZERO
    for _ in range(chunks):
        book_out, n_idx, n_used, consumed = _walk_book(offers, idx, used, chunk)
        if consumed < chunk:
            book_out = ZERO  # book can't take the full chunk; don't mix partials
        eff = chunk * (ONE - amm.fee)
        amm_out = y * eff / (x + eff)
        if book_out >= amm_out and book_out > 0:
            total_out += book_out
            from_book += book_out
            idx, used = n_idx, n_used
        else:
            total_out += amm_out
            from_amm += amm_out
            amm_fee += chunk * amm.fee
            x += chunk
            y -= amm_out
    return SwapResult(amount_in, total_out, transfer_fee, amm_fee, from_book, from_amm)


def max_input(edge: Edge) -> Decimal:
    """Largest input the edge can absorb. AMMs are effectively unbounded
    (price just gets worse), books are bounded by their total size."""
    if edge.amm is not None:
        return Decimal("1e30")
    return sum((o.in_amount for o in edge.offers), ZERO) * edge.transfer_rate_in


@dataclass
class LoopQuote:
    amount_in: Decimal
    amount_out: Decimal
    leg_outputs: List[Decimal]
    transfer_fees: List[Decimal]
    amm_fees: List[Decimal]
    filled: bool

    @property
    def gross_profit(self) -> Decimal:
        return self.amount_out - self.amount_in


def quote_loop(edges: Sequence[Edge], amount_in: Decimal) -> LoopQuote:
    amt = amount_in
    outs, tfees, afees = [], [], []
    filled = True
    for e in edges:
        if amt > max_input(e):
            filled = False
        r = swap(e, amt)
        outs.append(r.amount_out)
        tfees.append(r.transfer_fee_paid)
        afees.append(r.amm_fee_paid)
        amt = r.amount_out
        if amt <= 0:
            filled = False
            break
    return LoopQuote(amount_in, amt if filled else ZERO, outs, tfees, afees, filled)


def loop_marginal_rate(edges: Sequence[Edge]) -> Decimal:
    """Product of marginal rates. If <= 1 there is no profit even at tiny size."""
    r = ONE
    for e in edges:
        r *= e.marginal_rate()
        if r == 0:
            return ZERO
    return r


@dataclass
class SizedOpportunity:
    size: Decimal
    quote: LoopQuote
    net_profit: Decimal  # after network fee, before slippage buffer


def best_size(
    edges: Sequence[Edge],
    min_size: Decimal,
    max_size: Decimal,
    network_fee_xrp: Decimal,
    iterations: int = 28,
) -> Optional[SizedOpportunity]:
    """Find the input size that maximises net profit (golden-section search on
    a log scale - profit vs size is single-peaked for these markets)."""
    if max_size < min_size:
        return None

    def net(size: Decimal) -> tuple[Decimal, LoopQuote]:
        q = quote_loop(edges, size)
        if not q.filled:
            return Decimal("-1e30"), q
        return q.amount_out - size - network_fee_xrp, q

    import math

    lo, hi = math.log(float(min_size)), math.log(float(max_size))
    gr = (math.sqrt(5) - 1) / 2
    a, b = lo, hi
    c = b - gr * (b - a)
    d = a + gr * (b - a)
    cache: dict[float, tuple[Decimal, LoopQuote]] = {}

    def f(v: float):
        if v not in cache:
            cache[v] = net(Decimal(repr(math.exp(v))).quantize(Decimal("0.000001")))
        return cache[v]

    for _ in range(iterations):
        if f(c)[0] > f(d)[0]:
            b = d
        else:
            a = c
        c = b - gr * (b - a)
        d = a + gr * (b - a)
    candidates = [lo, hi, c, d]
    best_v = max(candidates, key=lambda v: f(v)[0])
    profit, q = f(best_v)
    if profit <= Decimal("-1e29"):
        return None
    return SizedOpportunity(size=q.amount_in, quote=q, net_profit=profit)
