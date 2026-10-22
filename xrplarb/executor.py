"""Turning an opportunity into ledger transactions.

ATOMIC MODE (preferred, needs the ledger's "Batch" feature):
  One Batch transaction with the All-Or-Nothing flag, containing two payments
  to ourselves:
    1. XRP  -> ... -> last token   (exact amount of last token, spend at most N XRP)
    2. last token -> XRP           (must return at least the "floor" amount)
  If either part can't complete at our prices, the ledger cancels BOTH and we
  only pay the tiny network fee (~0.00004 XRP). Payment 1 is itself a
  multi-hop path, which the ledger also executes all-or-nothing.

TWO-STEP MODE (only if Batch isn't available AND you opted in):
  The same two payments sent one after the other. Payment 1 is still atomic
  across all of its hops, but between payment 1 and 2 (about 4 seconds) we
  hold the last token. If payment 2 can't meet its floor, the recovery step
  sells the token back to XRP straight away at the best price available,
  within a loss limit, and alerts you.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

import asyncio

from xrpl.asyncio.ledger import get_latest_validated_ledger_sequence
from xrpl.asyncio.transaction import autofill_and_sign, submit
from xrpl.models.path import PathStep
from xrpl.models.requests import AccountTx, Tx
from xrpl.models.transactions import Batch, BatchFlag, Payment, PaymentFlag
from xrpl.utils import get_balance_changes
from xrpl.wallet import Wallet

from .amounts import ONE, XRP, ZERO, Asset, ROUND_DOWN, ROUND_UP, drops_to_xrp, make_amount_model
from .scanner import Opportunity

log = logging.getLogger(__name__)


@dataclass
class LegRecord:
    tx_hash: str
    sent_asset: str
    sent_amount: Decimal
    received_asset: str
    received_amount: Decimal
    label: str = "trade"


@dataclass
class ExecResult:
    ok: bool
    mode: str
    tx_hash: str = ""
    ledger_index: Optional[int] = None
    result_code: str = ""
    xrp_in: Decimal = ZERO  # XRP spent
    xrp_out: Decimal = ZERO  # XRP received back
    network_fee_xrp: Decimal = ZERO
    legs: List[LegRecord] = field(default_factory=list)
    leftover: Dict[Asset, Decimal] = field(default_factory=dict)  # tokens stuck after a failure
    note: str = ""

    @property
    def profit(self) -> Decimal:
        return self.xrp_out - self.xrp_in - self.network_fee_xrp


def _paths_for(opp: Opportunity) -> Optional[List[List[PathStep]]]:
    middle = opp.route.tokens[:-1]  # tokens between XRP and the last token
    if not middle:
        return None
    return [[PathStep(currency=t.currency, issuer=t.issuer) for t in middle]]


def build_payments(address: str, opp: Opportunity, inner: bool) -> List[Payment]:
    last = opp.route.tokens[-1]
    p1 = Payment(
        account=address,
        destination=address,
        amount=make_amount_model(last, opp.last_token_target, ROUND_DOWN),
        send_max=make_amount_model(XRP, opp.size_xrp, ROUND_DOWN),
        paths=_paths_for(opp),
        **({"fee": "0", "signing_pub_key": ""} if inner else {}),
    )
    # Ask for "as much XRP as possible" (partial payment), but never less than
    # the floor. The floor already includes the network fee + minimum profit.
    p2 = Payment(
        account=address,
        destination=address,
        amount=make_amount_model(XRP, opp.size_xrp * 3, ROUND_DOWN),
        send_max=make_amount_model(last, opp.last_token_target, ROUND_DOWN),
        deliver_min=make_amount_model(XRP, opp.xrp_floor, ROUND_UP),
        flags=PaymentFlag.TF_PARTIAL_PAYMENT,
        **({"fee": "0", "signing_pub_key": ""} if inner else {}),
    )
    return [p1, p2]


def build_atomic_batch(address: str, opp: Opportunity, fee_drops: int, last_ledger: Optional[int] = None) -> Batch:
    return Batch(
        account=address,
        flags=BatchFlag.TF_ALL_OR_NOTHING,
        raw_transactions=build_payments(address, opp, inner=True),
        fee=str(fee_drops),
        last_ledger_sequence=last_ledger,
    )


def _tx_fields(entry: Dict[str, Any]) -> Dict[str, Any]:
    return entry.get("tx_json") or entry.get("tx") or {}


def legs_from_meta(
    address: str, tx_hash: str, meta: Dict[str, Any], label: str = "trade", fee_xrp: Decimal = ZERO
) -> List[LegRecord]:
    """Turn a transaction's balance changes for our account into
    'sent X / received Y' rows (what a tax spreadsheet wants).

    fee_xrp: the network fee this transaction paid (0 for Batch inner
    payments). It's added back so trade amounts exclude the fee; fees are
    recorded separately."""
    sent: Optional[tuple] = None
    recv: Optional[tuple] = None
    for ch in get_balance_changes(meta):
        if ch["account"] != address:
            continue
        for b in ch["balances"]:
            asset = "XRP" if b["currency"] == "XRP" else f"{b['currency']}.{b['issuer']}"
            v = Decimal(b["value"])
            if asset == "XRP":
                v += fee_xrp
            if v < 0:
                sent = (asset, -v)
            elif v > 0:
                recv = (asset, v)
    if not sent and not recv:
        return []
    return [
        LegRecord(
            tx_hash=tx_hash,
            sent_asset=sent[0] if sent else "",
            sent_amount=sent[1] if sent else ZERO,
            received_asset=recv[0] if recv else "",
            received_amount=recv[1] if recv else ZERO,
            label=label,
        )
    ]


class Executor:
    # How many ledgers (~4s each) a transaction may wait before it expires.
    LEDGER_WINDOW = 4

    def __init__(self, net, wallet: Wallet):
        self.net = net
        self.wallet = wallet

    @property
    def address(self) -> str:
        return self.wallet.classic_address

    async def last_ledger(self) -> int:
        return await get_latest_validated_ledger_sequence(self.net.client) + self.LEDGER_WINDOW

    async def submit_tx(self, tx) -> Dict[str, Any]:
        """Sign, submit, and wait until the outcome is final. Never raises for
        a failed transaction - returns its result code instead, so failures
        get logged like everything else."""
        signed = await autofill_and_sign(tx, self.net.client, self.wallet)
        tx_hash = signed.get_hash()
        sub = await submit(signed, self.net.client)
        prelim = sub.result.get("engine_result", "")
        base = {"hash": tx_hash, "tx_json": {"Fee": signed.fee}, "validated": False}
        if prelim[:3] in ("tem", "tef", "tel"):
            # Rejected outright: never enters a ledger, no fee charged.
            return {**base, "tx_json": {"Fee": "0"}, "meta": {"TransactionResult": prelim}}
        lls = signed.last_ledger_sequence or 0
        while True:
            await asyncio.sleep(1.0)
            r = await self.net.client.request(Tx(transaction=tx_hash))
            if r.is_successful() and r.result.get("validated"):
                return r.result
            if await get_latest_validated_ledger_sequence(self.net.client) > lls:
                return {**base, "tx_json": {"Fee": "0"}, "meta": {"TransactionResult": f"expired({prelim})"}}

    async def _inner_results(self, batch_hash: str, ledger_index: int) -> List[Dict[str, Any]]:
        r = await self.net.request(
            AccountTx(account=self.address, ledger_index_min=ledger_index, ledger_index_max=ledger_index, limit=50)
        )
        found = []
        for entry in r.get("transactions", []):
            tx = _tx_fields(entry)
            if str(tx.get("ParentBatchID", "")).upper() == batch_hash.upper():
                found.append({"hash": entry.get("hash") or tx.get("hash"), "tx": tx, "meta": entry.get("meta", {})})
        return found

    # ---- atomic ----------------------------------------------------------
    async def execute_atomic(self, opp: Opportunity, fee_drops: int) -> ExecResult:
        batch = build_atomic_batch(self.address, opp, fee_drops, await self.last_ledger())
        res = ExecResult(ok=False, mode="atomic")
        try:
            result = await self.submit_tx(batch)
        except Exception as e:  # e.g. connection lost mid-submit; outcome unknown
            res.result_code = f"error:{type(e).__name__}"
            res.note = str(e)[:300]
            return res
        res.tx_hash = result.get("hash", "")
        res.ledger_index = result.get("ledger_index")
        meta = result.get("meta", {})
        res.result_code = meta.get("TransactionResult", "")
        res.network_fee_xrp = drops_to_xrp(_tx_fields(result).get("Fee", "0"))
        if res.result_code != "tesSUCCESS" or not res.ledger_index:
            res.note = "batch not applied"
            return res
        inner = await self._inner_results(res.tx_hash, res.ledger_index)
        codes = [i["meta"].get("TransactionResult") for i in inner]
        if len(inner) != 2 or any(c != "tesSUCCESS" for c in codes):
            # All-or-nothing: if the inner payments didn't both apply, none applied.
            res.result_code = "batch-cancelled:" + ",".join(c or "?" for c in codes) if codes else "batch-cancelled"
            res.note = "price moved or gap was taken before our transaction - nothing traded, only the fee was paid"
            return res
        for i in inner:
            res.legs.extend(legs_from_meta(self.address, i["hash"], i["meta"]))
        for leg in res.legs:
            if leg.sent_asset == "XRP":
                res.xrp_in += leg.sent_amount
            if leg.received_asset == "XRP":
                res.xrp_out += leg.received_amount
        res.ok = True
        return res

    # ---- two-step (non-atomic) -------------------------------------------
    async def execute_two_step(self, opp: Opportunity, fee_drops_each: int, recover) -> ExecResult:
        """`recover(asset, amount)` is an async callback that sells leftovers
        to XRP and returns an ExecResult for the recovery."""
        p1, p2 = build_payments(self.address, opp, inner=False)
        res = ExecResult(ok=False, mode="two-step")
        fee_xrp = drops_to_xrp(fee_drops_each)
        p1 = Payment.from_dict({**p1.to_dict(), "fee": str(fee_drops_each), "last_ledger_sequence": await self.last_ledger()})
        r1 = await self.submit_tx(p1)
        code1 = r1.get("meta", {}).get("TransactionResult", "")
        res.tx_hash = r1.get("hash", "")
        res.ledger_index = r1.get("ledger_index")
        res.network_fee_xrp += drops_to_xrp(_tx_fields(r1).get("Fee", "0"))
        if code1 != "tesSUCCESS":
            res.result_code = code1
            res.note = "first payment failed - nothing traded, only the fee was paid"
            return res
        res.legs.extend(legs_from_meta(self.address, res.tx_hash, r1["meta"], fee_xrp=fee_xrp))
        last = opp.route.tokens[-1]
        got_token = sum((l.received_amount for l in res.legs if l.received_asset == last.key), ZERO)
        res.xrp_in = sum((l.sent_amount for l in res.legs if l.sent_asset == "XRP"), ZERO)

        p2 = Payment.from_dict({**p2.to_dict(), "fee": str(fee_drops_each), "last_ledger_sequence": await self.last_ledger()})
        try:
            r2 = await self.submit_tx(p2)
            code2 = r2.get("meta", {}).get("TransactionResult", "")
            res.network_fee_xrp += drops_to_xrp(_tx_fields(r2).get("Fee", "0"))
        except Exception as e:
            r2, code2 = {}, f"error:{type(e).__name__}"
        if code2 == "tesSUCCESS":
            res.legs.extend(legs_from_meta(self.address, r2.get("hash", ""), r2["meta"], fee_xrp=fee_xrp))
            res.xrp_out = sum((l.received_amount for l in res.legs if l.received_asset == "XRP"), ZERO)
            res.result_code = "tesSUCCESS"
            res.ok = True
            return res

        # Recovery: we are holding `last` token. Sell it now.
        res.result_code = f"second-leg-failed:{code2}"
        rec = await recover(last, got_token)
        res.legs.extend(rec.legs)
        res.network_fee_xrp += rec.network_fee_xrp
        res.xrp_out = sum((l.received_amount for l in res.legs if l.received_asset == "XRP"), ZERO)
        res.leftover = rec.leftover
        res.note = "second leg failed; recovery " + ("sold the token back to XRP" if rec.ok else "COULD NOT sell everything")
        res.mode = "two-step+recovery"
        return res

    # ---- selling a token back to XRP -------------------------------------
    async def sell_to_xrp(
        self, asset: Asset, amount: Decimal, expected_xrp: Decimal, min_xrp: Decimal, fee_drops: int
    ) -> ExecResult:
        """Sell `amount` of a token for XRP, accepting no less than min_xrp.
        Uses a partial payment so it sells the whole amount at the best prices
        available rather than an exact target."""
        res = ExecResult(ok=False, mode="liquidation")
        if amount <= 0:
            res.ok = True
            return res
        p = Payment(
            account=self.address,
            destination=self.address,
            # upper bound on what we could receive; generous so it never limits the sale
            amount=make_amount_model(XRP, expected_xrp * 2 + ONE, ROUND_DOWN),
            send_max=make_amount_model(asset, amount, ROUND_DOWN),
            deliver_min=make_amount_model(XRP, max(min_xrp, Decimal("0.000001")), ROUND_UP),
            flags=PaymentFlag.TF_PARTIAL_PAYMENT,
            fee=str(fee_drops),
            last_ledger_sequence=await self.last_ledger(),
        )
        try:
            r = await self.submit_tx(p)
        except Exception as e:
            res.result_code = f"error:{type(e).__name__}"
            res.note = str(e)[:300]
            res.leftover = {asset: amount}
            return res
        res.tx_hash = r.get("hash", "")
        res.ledger_index = r.get("ledger_index")
        res.result_code = r.get("meta", {}).get("TransactionResult", "")
        res.network_fee_xrp = drops_to_xrp(_tx_fields(r).get("Fee", "0"))
        if res.result_code != "tesSUCCESS":
            res.leftover = {asset: amount}
            return res
        res.legs = legs_from_meta(
            self.address, res.tx_hash, r["meta"], label="liquidation", fee_xrp=drops_to_xrp(fee_drops)
        )
        sold = sum((l.sent_amount for l in res.legs if l.sent_asset == asset.key), ZERO)
        res.xrp_out = sum((l.received_amount for l in res.legs if l.received_asset == "XRP"), ZERO)
        if amount - sold > amount * Decimal("0.000001"):
            res.leftover = {asset: amount - sold}
        res.ok = not res.leftover
        return res
