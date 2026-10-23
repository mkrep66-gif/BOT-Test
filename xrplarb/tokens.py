"""Token safety review and token discovery.

For each token we check, in plain English:
  * Transfer fee   - the issuer takes a cut every time the token moves.
  * Freeze         - can the issuer freeze our balance (stuck tokens)?
  * Global freeze  - is the token frozen for everyone right now?
  * Clawback       - can the issuer take tokens back out of our wallet?
  * Require auth   - do we need the issuer's permission to hold it?
  * Issuer control - can the issuer still change any of the above later?
  * Liquidity      - is there enough real trading depth vs XRP, and what does
                     a buy-then-sell round trip actually cost?
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from xrpl.models.requests import LedgerData

from .amounts import ONE, XRP, ZERO, Asset
from .market import MarketFeed, MarketSnapshot
from .quote import swap
from .xrplnet import Net

log = logging.getLogger(__name__)

LSF_REQUIRE_AUTH = 0x00040000
LSF_DISABLE_MASTER = 0x00100000
NEAR_BEST_PRICE = Decimal("1.05")  # order-book depth counts orders within 5% of the best price
LSF_NO_FREEZE = 0x00200000
LSF_GLOBAL_FREEZE = 0x00400000
LSF_ALLOW_CLAWBACK = 0x80000000
BLACKHOLE_KEYS = {
    "rrrrrrrrrrrrrrrrrrrrrhoLvTp",
    "rrrrrrrrrrrrrrrrrrrrBZbvji",
    "rrrrrrrrrrrrrrrrrrrn5RM1rHd",
    "rrrrrrrrrrrrrrrrrrrrrrrrrrrrrrrr",
}

VERDICT_OK = "OK"
VERDICT_CAUTION = "CAUTION"
VERDICT_REJECT = "NOT RECOMMENDED"


@dataclass
class TokenReport:
    asset: Asset
    verdict: str = VERDICT_OK
    transfer_fee_pct: Decimal = ZERO
    can_freeze: bool = True
    global_freeze: bool = False
    clawback: bool = False
    require_auth: bool = False
    issuer_locked: bool = False
    issuer_exists: bool = True
    xrp_liquidity: Decimal = ZERO  # XRP on the AMM's XRP side + XRP in top of book
    roundtrip_cost_pct: Dict[str, Optional[Decimal]] = field(default_factory=dict)
    red_flags: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def snapshot(self) -> Dict[str, Any]:
        """Facts recorded at approval time; the bots re-check these and stop
        trading the token if any of them get worse."""
        return {
            "transfer_fee_pct": str(self.transfer_fee_pct),
            "can_freeze": self.can_freeze,
            "clawback": self.clawback,
            "require_auth": self.require_auth,
            "issuer_locked": self.issuer_locked,
        }


def _flag(flags: int, f: int) -> bool:
    return bool(flags & f)


def assess_issuer(report: TokenReport, info: Optional[Dict[str, Any]]) -> None:
    if info is None:
        report.issuer_exists = False
        report.red_flags.append("The issuer account does not exist on this network.")
        report.verdict = VERDICT_REJECT
        return
    flags = int(info.get("Flags", 0))
    rate = Decimal(int(info.get("TransferRate", 0) or 0))
    report.transfer_fee_pct = ((rate / Decimal(1_000_000_000)) - ONE) * 100 if rate else ZERO
    report.can_freeze = not _flag(flags, LSF_NO_FREEZE)
    report.global_freeze = _flag(flags, LSF_GLOBAL_FREEZE)
    report.clawback = _flag(flags, LSF_ALLOW_CLAWBACK)
    report.require_auth = _flag(flags, LSF_REQUIRE_AUTH)
    master_off = _flag(flags, LSF_DISABLE_MASTER)
    regkey = info.get("RegularKey")
    # A multi-signature list can still sign for the account, so it isn't locked
    # (e.g. RLUSD's issuer: master key off, controlled by a signer list).
    has_signers = bool(info.get("signer_lists"))
    report.issuer_locked = master_off and not has_signers and (regkey is None or regkey in BLACKHOLE_KEYS)

    if report.global_freeze:
        report.red_flags.append("FROZEN: the issuer has frozen this token for everyone right now. Nobody can trade it.")
    if report.require_auth:
        report.red_flags.append("Needs the issuer's permission to hold it - we can't use it freely.")
    if report.clawback:
        report.red_flags.append(
            "CLAWBACK: the issuer can take this token back out of any wallet at any time. "
            "If that happened while a bot held it, the bot would lose that value."
        )
    if report.transfer_fee_pct > 0:
        msg = (
            f"Transfer fee {report.transfer_fee_pct:.3f}%: the issuer takes this cut every time the token "
            "moves. It's included in every profit calculation, but it makes loops through this token harder to win."
        )
        (report.red_flags if report.transfer_fee_pct > Decimal("0.5") else report.notes).append(msg)
    if report.can_freeze:
        report.notes.append(
            "The issuer can freeze individual balances. Bots only hold a token for seconds, so the risk is "
            "small, but a freeze at the wrong moment could trap one trade's worth."
        )
    else:
        report.notes.append("The issuer has permanently given up the ability to freeze balances (good).")
    if report.issuer_locked:
        report.notes.append("The issuer account is locked ('blackholed'): its settings can never change (good).")
    else:
        report.notes.append(
            "The issuer can still change settings (e.g. raise the transfer fee up to 100%). The bots re-check "
            "the fee and flags every few minutes and stop using the token if anything gets worse."
        )

    if report.global_freeze or report.require_auth or report.clawback or report.transfer_fee_pct > 1:
        report.verdict = VERDICT_REJECT
    elif report.transfer_fee_pct > 0 or report.can_freeze or not report.issuer_locked:
        report.verdict = VERDICT_CAUTION


def assess_liquidity(report: TokenReport, snap: MarketSnapshot, min_xrp_liquidity: Decimal) -> None:
    buy = snap.edge(XRP, report.asset)
    sell = snap.edge(report.asset, XRP)
    depth = ZERO
    for e in (buy, sell):
        if e is None:
            continue
        if e.amm is not None:
            depth = max(depth, e.amm.pool_in if e.src.is_xrp else e.amm.pool_out)
    if buy is not None and buy.offers:
        # Only count orders near the best price: one absurdly priced order
        # (seen on mainnet: "500 million XRP" of depth) isn't real liquidity.
        best = buy.offers[0].price
        depth += sum((o.in_amount for o in buy.offers if o.price <= best * NEAR_BEST_PRICE), ZERO)
    report.xrp_liquidity = depth

    for size in (Decimal(20), Decimal(100)):
        if not (buy and sell and buy.has_liquidity() and sell.has_liquidity()):
            report.roundtrip_cost_pct[str(size)] = None
            continue
        got = swap(buy, size).amount_out
        back = swap(sell, got).amount_out if got > 0 else ZERO
        report.roundtrip_cost_pct[str(size)] = (ONE - back / size) * 100 if back > 0 else Decimal(100)

    rt100 = report.roundtrip_cost_pct.get("100")
    if depth < min_xrp_liquidity or rt100 is None:
        report.red_flags.append(
            f"Thin market: only about {depth:,.0f} XRP of liquidity against XRP "
            f"(we want at least {min_xrp_liquidity:,.0f})."
        )
        report.verdict = VERDICT_REJECT
    elif rt100 > 3:
        report.red_flags.append(
            f"Buying and immediately selling 100 XRP worth loses {rt100:.2f}% - prices move a lot with size."
        )
        if report.verdict == VERDICT_OK:
            report.verdict = VERDICT_CAUTION


async def review_tokens(
    net: Net, tokens: List[Asset], min_xrp_liquidity: Decimal = Decimal(5000)
) -> List[TokenReport]:
    feed = MarketFeed(net, depth=50)
    snap = await feed.snapshot(tokens)
    issuers = sorted({t.issuer for t in tokens})
    infos = await asyncio.gather(*(net.account_info(i) for i in issuers))
    by_issuer = dict(zip(issuers, infos))
    reports = []
    for t in tokens:
        r = TokenReport(asset=t)
        assess_issuer(r, by_issuer.get(t.issuer))
        assess_liquidity(r, snap, min_xrp_liquidity)
        reports.append(r)
    return reports


def report_markdown(reports: List[TokenReport], network: str) -> str:
    lines = [
        f"# Token safety review ({network})",
        "",
        "Each token below was checked for transfer fees, freeze ability, clawback, issuer control and real "
        "liquidity. **Nothing is traded until you confirm a token in writing.**",
        "",
        "| Token | Issuer | Verdict | Transfer fee | Can freeze | Clawback | XRP liquidity | Round trip 100 XRP |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in reports:
        rt = r.roundtrip_cost_pct.get("100")
        lines.append(
            f"| {r.asset.symbol} | `{r.asset.issuer}` | **{r.verdict}** | {r.transfer_fee_pct:.3f}% | "
            f"{'yes' if r.can_freeze else 'no'} | {'YES' if r.clawback else 'no'} | {r.xrp_liquidity:,.0f} | "
            f"{'n/a' if rt is None else f'-{rt:.2f}%'} |"
        )
    lines.append("")
    for r in reports:
        lines.append(f"## {r.asset.symbol} ({r.asset.issuer}) - {r.verdict}")
        for f in r.red_flags:
            lines.append(f"- ⚠️ {f}")
        for n in r.notes:
            lines.append(f"- {n}")
        lines.append("")
    return "\n".join(lines)


async def discover_tokens(
    net: Net, max_tokens: int = 40, min_xrp_liquidity: Decimal = Decimal(5000), max_pages: int = 400
) -> Tuple[List[Asset], List[Tuple[Asset, Asset]]]:
    """Find candidate tokens by scanning every AMM pool on the ledger.

    Returns (tokens paired with XRP with real liquidity, token/token AMM pairs among them).
    Read-only: needs no wallet and costs nothing.
    """
    xrp_pools: List[Tuple[Asset, Decimal, str]] = []  # (token, lp balance, amm account)
    token_pairs: List[Tuple[Asset, Asset]] = []
    marker = None
    for _ in range(max_pages):
        r = await net.request(LedgerData(type="amm", ledger_index="validated", limit=256, marker=marker))
        for obj in r.get("state", []):
            a = obj.get("Asset", {})
            b = obj.get("Asset2", {})
            asset_a = XRP if a.get("currency") == "XRP" else Asset(a["currency"], a["issuer"])
            asset_b = XRP if b.get("currency") == "XRP" else Asset(b["currency"], b["issuer"])
            lp = obj.get("LPTokenBalance", {})
            lp_val = Decimal(lp.get("value", "0")) if isinstance(lp, dict) else ZERO
            if asset_a.is_xrp != asset_b.is_xrp:
                tok = asset_b if asset_a.is_xrp else asset_a
                xrp_pools.append((tok, lp_val, obj.get("Account", "")))
            elif not asset_a.is_xrp:
                token_pairs.append((asset_a, asset_b))
        marker = r.get("marker")
        if not marker:
            break

    # Rough pre-rank by LP token supply, then measure the real XRP side.
    xrp_pools.sort(key=lambda t: t[1], reverse=True)
    shortlist = xrp_pools[: max_tokens * 4]
    balances = await asyncio.gather(*(net.xrp_balance(acct) for _, _, acct in shortlist), return_exceptions=True)
    ranked = [
        (tok, bal) for (tok, _, _), bal in zip(shortlist, balances) if isinstance(bal, Decimal) and bal >= min_xrp_liquidity
    ]
    ranked.sort(key=lambda t: t[1], reverse=True)
    tokens = [t for t, _ in ranked[:max_tokens]]
    tset = set(tokens)
    pairs = [(a, b) for a, b in token_pairs if a in tset and b in tset]
    return tokens, pairs


async def probe_book_pairs(net: Net, tokens: List[Asset]) -> List[Tuple[Asset, Asset]]:
    """Find token/token order books that actually have offers (one request per
    direction per pair - keep the token list short)."""
    candidates = [(a, b) for i, a in enumerate(tokens) for b in tokens[i + 1 :]]

    async def has_book(a: Asset, b: Asset) -> bool:
        for gets, pays in ((a, b), (b, a)):
            try:
                if await net.book_offers(gets=gets, pays=pays, limit=1):
                    return True
            except Exception:
                pass
        return False

    found = await asyncio.gather(*(has_book(a, b) for a, b in candidates))
    return [pair for pair, ok in zip(candidates, found) if ok]
