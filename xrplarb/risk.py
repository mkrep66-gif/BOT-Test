"""Risk limits: honest wallet valuation and the 10%-a-day loss cap."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, Optional, Tuple

from .amounts import ZERO, Asset
from .db import DB
from .localtime import day_start_utc, today_local
from .market import MarketSnapshot, liquidation_value


@dataclass
class Valuation:
    xrp: Decimal
    tokens: Dict[Asset, Decimal] = field(default_factory=dict)  # amounts held
    tokens_value_xrp: Dict[Asset, Decimal] = field(default_factory=dict)  # what they'd sell for now

    @property
    def total(self) -> Decimal:
        return self.xrp + sum(self.tokens_value_xrp.values(), ZERO)


def value_holdings(xrp: Decimal, tokens: Dict[Asset, Decimal], snap: MarketSnapshot) -> Valuation:
    """Tokens are valued at what they would actually sell for into the
    current order books / AMM pools (after fees and price impact) - never at
    face value. A token nobody will buy is worth 0."""
    held = {a: v for a, v in tokens.items() if v > 0}
    return Valuation(xrp=xrp, tokens=held, tokens_value_xrp=liquidation_value(snap, held))


def daily_loss(
    db: DB, network: str, bot: str, current_total: Decimal, day: Optional[str] = None
) -> Tuple[Decimal, Decimal, Optional[Decimal]]:
    """Returns (loss_xrp, loss_fraction, start_value) for today (since your
    local midnight).

    Money you moved in or out (deposits, sweeps to your vault) is not counted
    as profit or loss."""
    day = day or today_local()
    start, start_ts = db.day_start_value(network, bot, day)
    if start is None or start <= 0:
        return ZERO, ZERO, start
    transfers = db.net_transfers_since(network, bot, max(start_ts, day_start_utc(day)))
    loss = start + transfers - current_total
    return loss, loss / start, start


def loss_cap_hit(loss_fraction: Decimal, limit: Decimal) -> bool:
    return loss_fraction > limit
