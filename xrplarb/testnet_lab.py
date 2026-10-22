"""Testnet lab: makes our own practice market on Testnet.

Testnet has almost no real tokens or trading, so to prove the bots work end
to end we create:
  * two test token issuers (one with a 0.2% transfer fee, to test fee handling)
  * three tokens: TUS, TEU (issuer A) and TGB (issuer B) - 3-letter codes, like test USD/EUR/GBP
  * AMM pools against XRP, plus two token/token pools that are deliberately
    mispriced, so profitable loops exist
  * a "noise" mode that keeps knocking prices out of line again

This proves the plumbing (finding, pricing, atomic execution, logging,
limits). It says NOTHING about real-world profitability - that's what the
mainnet watch-only run is for.
"""

from __future__ import annotations

import asyncio
import logging
import random
from decimal import Decimal
from typing import Dict, List

from xrpl.asyncio.wallet import generate_faucet_wallet
from xrpl.models.amounts import IssuedCurrencyAmount
from xrpl.models.transactions import (
    AccountSet,
    AccountSetAsfFlag,
    AMMCreate,
    Payment,
    PaymentFlag,
    TrustSet,
    TrustSetFlag,
)

from . import secrets_store
from .amounts import Asset, xrp_to_drops
from .config import NETWORKS, load_token_entries, save_tokens
from .executor import Executor
from .wallets import create_wallet, load_wallet
from .xrplnet import Net

log = logging.getLogger(__name__)

TOKENS = {"TUS": "lab_issuer_a", "TEU": "lab_issuer_a", "TGB": "lab_issuer_b"}
# price of 1 token in XRP
PRICES = {"TUS": Decimal("2.0"), "TEU": Decimal("2.2"), "TGB": Decimal("2.6")}
# token/token pools priced this far off the fair cross rate (creates gaps)
SKEWS = {("TUS", "TEU"): Decimal("0.04"), ("TEU", "TGB"): Decimal("-0.03")}


def _ic(cur: str, issuer: str, value: Decimal | str) -> IssuedCurrencyAmount:
    return IssuedCurrencyAmount(currency=cur, issuer=issuer, value=str(value))


async def _ok(ex: Executor, tx, what: str) -> None:
    r = await ex.submit_tx(tx)
    code = r.get("meta", {}).get("TransactionResult")
    log.info("%s: %s", what, code)
    if code not in ("tesSUCCESS", "tecDUPLICATE"):
        raise RuntimeError(f"{what} failed: {code}")


def lab_assets() -> Dict[str, Asset]:
    addrs = {w["name"]: w["address"] for w in secrets_store.wallets("testnet")}
    return {cur: Asset(cur, addrs[iss]) for cur, iss in TOKENS.items()}


async def lab_setup(net: Net, pool_xrp: Decimal = Decimal(25)) -> List[str]:
    out = []
    for name in ("lab_issuer_a", "lab_issuer_b", "lab_mm"):
        out.append(f"{name}: {await create_wallet(net, 'testnet', name)}")
    mm = load_wallet("testnet", "lab_mm")
    faucet = NETWORKS["testnet"]["faucet"]
    while await net.xrp_balance(mm.classic_address) < pool_xrp * 5 + 20:
        await generate_faucet_wallet(await net.working_client(), wallet=mm, faucet_host=faucet, usage_context="xrplarb-lab")

    ia, ib = load_wallet("testnet", "lab_issuer_a"), load_wallet("testnet", "lab_issuer_b")
    for w in (ia, ib):
        ex = Executor(net, w)
        await _ok(ex, AccountSet(account=w.classic_address, set_flag=AccountSetAsfFlag.ASF_DEFAULT_RIPPLE), "default ripple")
    await _ok(Executor(net, ia), AccountSet(account=ia.classic_address, set_flag=AccountSetAsfFlag.ASF_NO_FREEZE), "issuer A no-freeze")
    await _ok(Executor(net, ib), AccountSet(account=ib.classic_address, transfer_rate=1_002_000_000), "issuer B 0.2% fee")

    assets = lab_assets()
    mx = Executor(net, mm)
    for cur, a in assets.items():
        await _ok(mx, TrustSet(account=mm.classic_address, limit_amount=_ic(cur, a.issuer, "1000000000000"),
                               flags=TrustSetFlag.TF_SET_NO_RIPPLE), f"mm trust {cur}")
        iw = ia if TOKENS[cur] == "lab_issuer_a" else ib
        await _ok(Executor(net, iw), Payment(account=iw.classic_address, destination=mm.classic_address,
                                             amount=_ic(cur, a.issuer, "100000")), f"issue {cur}")

    for cur, a in assets.items():
        tok = pool_xrp / PRICES[cur]
        await _ok(mx, AMMCreate(account=mm.classic_address, amount=str(xrp_to_drops(pool_xrp)),
                                amount2=_ic(cur, a.issuer, tok.quantize(Decimal("0.000001"))), trading_fee=300),
                  f"AMM XRP/{cur}")
    for (c1, c2), skew in SKEWS.items():
        a1, a2 = assets[c1], assets[c2]
        n1 = Decimal(20)
        fair = PRICES[c1] / PRICES[c2]  # c2 per c1
        n2 = n1 * fair * (1 + skew)
        await _ok(mx, AMMCreate(account=mm.classic_address, amount=_ic(c1, a1.issuer, n1),
                                amount2=_ic(c2, a2.issuer, n2.quantize(Decimal("0.000001"))), trading_fee=300),
                  f"AMM {c1}/{c2}")

    # Put the lab tokens on the testnet token list (NOT approved - you confirm).
    entries = {(e["currency"], e["issuer"]): e for e in load_token_entries("testnet")}
    for cur, a in assets.items():
        entries.setdefault((a.currency, a.issuer), {
            "name": f"{cur} (testnet lab token)", "currency": a.currency, "issuer": a.issuer, "approved": False,
        })
    save_tokens("testnet", list(entries.values()))
    from .config import save_token_pairs

    save_token_pairs("testnet", [(assets[c1], assets[c2]) for c1, c2 in SKEWS])
    out.append("Created 3 lab tokens, 3 XRP pools and 2 skewed token/token pools.")
    return out


async def lab_noise(net: Net, every_s: float = 60, rounds: int = 0) -> None:
    """Every so often, push a token/token pool out of line again so the
    bots have something to find. rounds=0 means forever."""
    mm = load_wallet("testnet", "lab_mm")
    ex = Executor(net, mm)
    assets = lab_assets()
    n = 0
    while rounds == 0 or n < rounds:
        (c1, c2) = random.choice(list(SKEWS))
        if random.random() < 0.5:
            c1, c2 = c2, c1
        amt = Decimal(random.choice(["0.5", "1", "2"]))
        a1, a2 = assets[c1], assets[c2]
        tx = Payment(
            account=mm.classic_address, destination=mm.classic_address,
            amount=_ic(c2, a2.issuer, "1000"), send_max=_ic(c1, a1.issuer, amt),
            flags=PaymentFlag.TF_PARTIAL_PAYMENT,
        )
        try:
            r = await ex.submit_tx(tx)
            log.info("noise %s->%s %s: %s", c1, c2, amt, r.get("meta", {}).get("TransactionResult"))
        except Exception as e:
            log.warning("noise failed: %s", e)
        n += 1
        await asyncio.sleep(every_s)
