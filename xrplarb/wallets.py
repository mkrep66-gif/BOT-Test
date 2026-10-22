"""Wallet setup and housekeeping.

* Testnet wallets are created and funded with free test XRP from the faucet.
* Mainnet wallets are NEVER created unless you explicitly pass the approval
  flag on the command line (your rule: ask first).
* Each bot wallet keeps at most `working_balance_xrp` (100 XRP) to trade
  with, plus the ledger's required reserve. Anything above that is swept to
  your own "vault" address, or simply left untouched if no vault is set.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Dict, List, Optional

from xrpl.asyncio.wallet import generate_faucet_wallet
from xrpl.models.amounts import IssuedCurrencyAmount
from xrpl.models.transactions import Payment, TrustSet, TrustSetFlag
from xrpl.wallet import Wallet

from . import secrets_store
from .amounts import ZERO, Asset, drops_to_xrp, xrp_to_drops
from .config import NETWORKS
from .executor import Executor
from .xrplnet import Net

log = logging.getLogger(__name__)

TRUST_LIMIT = "1000000000000000"


def bot_name(i: int) -> str:
    return f"bot{i}"


def load_wallet(network: str, name: str) -> Wallet:
    for w in secrets_store.wallets(network):
        if w["name"] == name:
            return Wallet.from_seed(w["seed"])
    raise KeyError(f"no wallet named {name} on {network}")


def wallet_addresses(network: str) -> Dict[str, str]:
    return {w["name"]: w["address"] for w in secrets_store.wallets(network)}


async def create_wallet(net: Net, network: str, name: str, mainnet_approved: bool = False) -> str:
    """Create (and on testnet, fund) a wallet. Returns its public address.
    The secret key goes straight into the private settings file."""
    existing = wallet_addresses(network)
    if name in existing:
        return existing[name]
    if network == "mainnet":
        if not mainnet_approved:
            raise PermissionError(
                "Refusing to create a mainnet wallet without written approval "
                "(pass --i-have-written-approval after the owner says so)."
            )
        w = Wallet.create()  # generated offline; the owner must fund it
    else:
        w = await generate_faucet_wallet(net.client, faucet_host=NETWORKS[network]["faucet"], usage_context="xrplarb")
    secrets_store.add_wallet(network, name, w.classic_address, w.seed)
    log.info("created %s wallet %s: %s", network, name, w.classic_address)
    return w.classic_address


async def ensure_trustlines(net: Net, wallet: Wallet, tokens: List[Asset], fee_drops: int = 12) -> List[str]:
    """Open a trust line for every approved token we don't have one for yet.
    Each costs a 0.2 XRP reserve (locked, not spent; refunded if removed)."""
    have = {Asset(l["currency"], l["account"]) for l in await net.trust_lines(wallet.classic_address)}
    ex = Executor(net, wallet)
    results = []
    for t in tokens:
        if t in have:
            continue
        tx = TrustSet(
            account=wallet.classic_address,
            limit_amount=IssuedCurrencyAmount(currency=t.currency, issuer=t.issuer, value=TRUST_LIMIT),
            flags=TrustSetFlag.TF_SET_NO_RIPPLE,
            fee=str(fee_drops),
            last_ledger_sequence=await ex.last_ledger(),
        )
        r = await ex.submit_tx(tx)
        code = r.get("meta", {}).get("TransactionResult", "")
        results.append(f"{t.short}: {code}")
    return results


def required_reserve(base: Decimal, inc: Decimal, owner_count: int) -> Decimal:
    return base + inc * owner_count


async def sweep_excess(
    net: Net,
    wallet: Wallet,
    working_cap: Decimal,
    reserve_base: Decimal,
    reserve_inc: Decimal,
    vault: Optional[str],
    margin: Decimal,
    fee_drops: int = 12,
) -> Optional[Decimal]:
    """If the wallet holds more than cap + reserve + margin, send the excess
    to the vault. Returns the swept amount (or None)."""
    if not vault:
        return None
    info = await net.account_info(wallet.classic_address)
    if not info:
        return None
    bal = drops_to_xrp(info["Balance"])
    reserve = required_reserve(reserve_base, reserve_inc, int(info.get("OwnerCount", 0)))
    keep = working_cap + reserve
    excess = bal - keep
    if excess <= margin:
        return None
    ex = Executor(net, wallet)
    tx = Payment(
        account=wallet.classic_address,
        destination=vault,
        amount=str(xrp_to_drops(excess)),
        fee=str(fee_drops),
        last_ledger_sequence=await ex.last_ledger(),
    )
    r = await ex.submit_tx(tx)
    if r.get("meta", {}).get("TransactionResult") == "tesSUCCESS":
        return excess
    return None


async def spendable_xrp(net: Net, address: str, reserve_base: Decimal, reserve_inc: Decimal, working_cap: Decimal) -> Decimal:
    """XRP the bot may put into a trade: balance minus reserve minus a small
    fee cushion, capped at the working balance."""
    info = await net.account_info(address)
    if not info:
        return ZERO
    bal = drops_to_xrp(info["Balance"])
    reserve = required_reserve(reserve_base, reserve_inc, int(info.get("OwnerCount", 0)))
    free = bal - reserve - Decimal("0.5")
    return max(ZERO, min(free, working_cap))
