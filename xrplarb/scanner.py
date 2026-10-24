"""Evaluates routes against a market snapshot and decides which are real,
profitable-after-all-costs opportunities."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional, Sequence

from .amounts import ONE, ZERO, drops_to_xrp
from .market import MarketSnapshot
from .quote import Edge, LoopQuote, best_size, loop_marginal_rate, quote_loop, swap
from .routes import Route

# A Batch with two inner payments costs (2 + 2) x the per-transaction fee.
ATOMIC_FEE_UNITS = 4
# Without Batch: two separate payments.
TWO_STEP_FEE_UNITS = 2


def fee_drops(snap: MarketSnapshot, units: int, margin: Decimal = Decimal("1.2")) -> int:
    per_tx = Decimal(snap.base_fee_drops) * max(snap.load_factor, ONE) * margin
    return int(math.ceil(per_tx)) * units


@dataclass
class Opportunity:
    route: Route
    edges: List[Edge]
    ledger_index: Optional[int]
    size_xrp: Decimal = ZERO
    gross_profit: Decimal = ZERO  # before network fee
    network_fee_xrp: Decimal = ZERO
    net_profit: Decimal = ZERO  # after network + AMM + transfer fees, at simulated prices
    safe_profit: Decimal = ZERO  # after also applying the slippage buffer
    last_token_target: Decimal = ZERO  # exact amount of the last token we will buy
    xrp_floor: Decimal = ZERO  # the minimum XRP we accept back - else the whole thing cancels
    quote: Optional[LoopQuote] = None
    marginal_rate: Decimal = ZERO
    executable: bool = False
    reason: str = ""


def evaluate(
    route: Route,
    snap: MarketSnapshot,
    *,
    max_size: Decimal,
    min_size: Decimal,
    min_profit: Decimal,
    slippage_buffer: Decimal,
    max_fee_drops: int,
    fee_units: int = ATOMIC_FEE_UNITS,
    quick_reject: bool = False,
) -> Optional[Opportunity]:
    """Returns None when there's no gap at all (the normal case). Returns an
    Opportunity - executable or not, with a reason - when there is a gap."""
    edges: List[Edge] = []
    for a, b in zip(route.assets, route.assets[1:]):
        e = snap.edge(a, b)
        if e is None or not e.has_liquidity():
            return None
        edges.append(e)

    rate = loop_marginal_rate(edges)
    if rate <= ONE:
        return None

    opp = Opportunity(route=route, edges=edges, ledger_index=snap.ledger_index, marginal_rate=rate)
    fdrops = fee_drops(snap, fee_units)
    opp.network_fee_xrp = drops_to_xrp(fdrops)

    if max_size < min_size:
        opp.reason = "not enough free XRP in the wallet for the minimum trade size"
        opp.size_xrp = max_size
        return opp

    # Each leg gives less per unit as the size grows, so a loop's profit is a
    # concave function of size that starts at 0. If even the smallest size we
    # would trade loses money, every larger size loses more: skip the (costly)
    # size search. This rules out most gaps that only exist at the very top of
    # an order book (same answers, ~50x faster).
    probe = quote_loop(edges, min_size) if quick_reject else None
    if probe is not None and probe.filled and probe.gross_profit <= 0:
        opp.size_xrp, opp.quote = min_size, probe
        opp.gross_profit = probe.gross_profit
        opp.net_profit = probe.gross_profit - opp.network_fee_xrp
        opp.reason = "gap exists only at tiny size; any real trade size loses money"
        return opp

    sized = best_size(edges, min_size, max_size, opp.network_fee_xrp)
    if sized is None:
        opp.reason = "markets too thin to fill even the minimum trade size"
        return opp
    opp.size_xrp = sized.size
    opp.quote = sized.quote
    opp.gross_profit = sized.quote.gross_profit
    opp.net_profit = sized.net_profit

    if fdrops > max_fee_drops:
        opp.reason = f"network fees too high right now ({fdrops} drops > limit {max_fee_drops})"
        return opp
    if opp.gross_profit <= 0:
        opp.reason = "gap exists only at tiny size; any real trade size loses money"
        return opp
    if opp.net_profit <= 0:
        opp.reason = "gap too small to cover the network fee"
        return opp

    # Slippage buffer: we buy slightly less of the last token than the simulation
    # says we could, so small price moves before our transaction lands don't
    # cancel it. Then re-check profit with that smaller amount.
    last_token_out = sized.quote.leg_outputs[-2]
    target = last_token_out * (ONE - slippage_buffer)
    back = swap(edges[-1], target).amount_out
    opp.last_token_target = target
    opp.safe_profit = back - opp.size_xrp - opp.network_fee_xrp
    opp.xrp_floor = opp.size_xrp + opp.network_fee_xrp + min_profit
    if opp.safe_profit < min_profit:
        opp.reason = (
            f"profit after fees and {slippage_buffer * 100:.2f}% slippage buffer "
            f"({opp.safe_profit:.6f} XRP) is below the {min_profit} XRP minimum"
        )
        return opp
    opp.executable = True
    opp.reason = "profitable after all costs"
    return opp


def scan(
    routes: Sequence[Route],
    snap: MarketSnapshot,
    **kw,
) -> List[Opportunity]:
    out = []
    for r in routes:
        o = evaluate(r, snap, **kw)
        if o is not None:
            out.append(o)
    out.sort(key=lambda o: (o.executable, o.safe_profit), reverse=True)
    return out
