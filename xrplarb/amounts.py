"""Assets and amount helpers.

All money math uses Decimal. XRP is converted to/from integer drops only at the
edge where we talk to the ledger (1 XRP = 1,000,000 drops).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_UP, Decimal, getcontext
from typing import Any, Optional, Union

getcontext().prec = 40

DROPS_PER_XRP = Decimal(1_000_000)
ZERO = Decimal(0)
ONE = Decimal(1)


@dataclass(frozen=True, order=True)
class Asset:
    """XRP (issuer None) or an issued token identified by currency + issuer."""

    currency: str
    issuer: Optional[str] = None

    @property
    def is_xrp(self) -> bool:
        return self.currency == "XRP" and self.issuer is None

    @property
    def key(self) -> str:
        return "XRP" if self.is_xrp else f"{self.currency}.{self.issuer}"

    @property
    def symbol(self) -> str:
        """Human readable currency code (decodes 40-char hex codes)."""
        return currency_symbol(self.currency)

    @property
    def short(self) -> str:
        if self.is_xrp:
            return "XRP"
        return f"{self.symbol}.{self.issuer[:6]}"

    def to_xrpl(self) -> dict:
        """Currency spec used by book_offers / amm_info requests."""
        if self.is_xrp:
            return {"currency": "XRP"}
        return {"currency": self.currency, "issuer": self.issuer}

    def path_step(self) -> dict:
        return {"currency": self.currency, "issuer": self.issuer}

    @staticmethod
    def from_key(key: str) -> "Asset":
        if key == "XRP":
            return XRP
        currency, issuer = key.split(".", 1)
        return Asset(currency, issuer)


XRP = Asset("XRP", None)


def currency_symbol(code: str) -> str:
    if len(code) == 40:
        try:
            raw = bytes.fromhex(code)
            text = raw.rstrip(b"\x00").decode("utf-8")  # names can include emoji, e.g. 🌊JeetTide
            if text and text.isprintable():
                return text
        except (ValueError, UnicodeDecodeError):
            pass
        return code[:8] + "…"
    return code


def xrp_to_drops(xrp: Decimal, rounding=ROUND_DOWN) -> int:
    return int((xrp * DROPS_PER_XRP).to_integral_value(rounding=rounding))


def drops_to_xrp(drops: Union[int, str]) -> Decimal:
    return Decimal(int(drops)) / DROPS_PER_XRP


def parse_amount(amount: Any) -> tuple[Asset, Decimal]:
    """Parse an XRPL amount (drops string or {currency, issuer, value})."""
    if isinstance(amount, str):
        return XRP, drops_to_xrp(amount)
    if isinstance(amount, dict) and amount.get("currency") == "XRP" and "issuer" not in amount:
        return XRP, Decimal(amount["value"])
    return Asset(amount["currency"], amount["issuer"]), Decimal(amount["value"])


def round_sig(value: Decimal, sig: int = 15, rounding=ROUND_DOWN) -> Decimal:
    """Round to `sig` significant digits (XRPL token amounts allow 15)."""
    if value == 0:
        return ZERO
    exp = value.adjusted() - sig + 1
    q = Decimal(1).scaleb(exp)
    return value.quantize(q, rounding=rounding)


def fmt_token_value(value: Decimal, rounding=ROUND_DOWN) -> str:
    v = round_sig(value, 15, rounding)
    s = format(v.normalize(), "f")
    return s


def make_amount(asset: Asset, value: Decimal, rounding=ROUND_DOWN) -> Union[str, dict]:
    """Build an XRPL amount. Rounds DOWN by default (never promise more than we have)."""
    if asset.is_xrp:
        return str(xrp_to_drops(value, rounding))
    return {
        "currency": asset.currency,
        "issuer": asset.issuer,
        "value": fmt_token_value(value, rounding),
    }


def make_amount_model(asset: Asset, value: Decimal, rounding=ROUND_DOWN):
    """Same as make_amount, but returns the xrpl-py model type."""
    from xrpl.models.amounts import IssuedCurrencyAmount

    raw = make_amount(asset, value, rounding)
    if isinstance(raw, str):
        return raw
    return IssuedCurrencyAmount(currency=raw["currency"], issuer=raw["issuer"], value=raw["value"])


__all__ = [
    "Asset",
    "XRP",
    "ZERO",
    "ONE",
    "ROUND_DOWN",
    "ROUND_UP",
    "xrp_to_drops",
    "drops_to_xrp",
    "parse_amount",
    "round_sig",
    "make_amount",
    "make_amount_model",
    "currency_symbol",
]
