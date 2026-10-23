"""One trading bot: one wallet, one group of routes.

Each time the supervisor hands it a fresh market snapshot, the bot:
  1. checks it's allowed to trade (kill switch, loss cap, halts)
  2. evaluates its routes; logs every gap it sees, traded or not, with why
  3. executes the single best profitable loop (atomically where possible)
  4. logs the trade, legs and fees; re-values the wallet honestly
  5. stops itself if today's loss exceeds the cap
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from xrpl.wallet import Wallet

from .alerts import Alerter
from .amounts import XRP, ZERO, Asset, drops_to_xrp
from .config import Settings
from .db import DB
from .executor import Executor, ExecResult, _tx_fields
from .instance import check_wallet
from .market import MarketFeed, MarketSnapshot
from .quote import swap
from .risk import daily_loss, loss_cap_hit, value_holdings
from .routes import Route
from .scanner import ATOMIC_FEE_UNITS, TWO_STEP_FEE_UNITS, Opportunity, fee_drops, scan
from .wallets import required_reserve, sweep_excess

log = logging.getLogger(__name__)

LIQUIDATION_STEPS = [Decimal("0.005"), Decimal("0.02"), Decimal("0.05")]
MAX_ROUTE_COOLDOWN_S = 1800.0
RIPPLE_EPOCH = 946684800  # ledger times count seconds from 2000-01-01


class MarketLocks:
    """Two bots never trade the same market at the same moment."""

    def __init__(self):
        self._held: Dict[Tuple[str, str], str] = {}

    def try_acquire(self, bot: str, keys: List[Tuple[str, str]]) -> Optional[str]:
        for k in keys:
            owner = self._held.get(k) or self._held.get((k[1], k[0]))
            if owner and owner != bot:
                return owner
        for k in keys:
            self._held[k] = bot
        return None

    def release(self, bot: str) -> None:
        for k in [k for k, v in self._held.items() if v == bot]:
            del self._held[k]


class Bot:
    def __init__(
        self,
        name: str,
        wallet: Wallet,
        routes: List[Route],
        settings: Settings,
        net,
        db: DB,
        alerter: Alerter,
        locks: MarketLocks,
        feed: MarketFeed,
        atomic_available: bool,
    ):
        self.name = name
        self.wallet = wallet
        self.routes = routes
        self.s = settings
        self.net = net
        self.db = db
        self.alerter = alerter
        self.locks = locks
        self.feed = feed
        self.atomic = atomic_available
        self.ex = Executor(net, wallet)
        self.busy = False
        self.halted_reason: Optional[str] = None
        self.xrp_balance = ZERO
        self.owner_count = 0
        self.tokens: Dict[Asset, Decimal] = {}
        self._last_refresh = 0.0
        self._last_missed: Dict[str, Tuple[str, Decimal]] = {}
        self.errors: List[float] = []
        self._route_fails: Dict[str, Tuple[int, float]] = {}  # route -> (failures in a row, retry after)
        self._offer_seen: Dict[int, float] = {}  # stray order sequence -> first seen
        self._started = time.time()

    @property
    def address(self) -> str:
        return self.wallet.classic_address

    @property
    def network(self) -> str:
        return self.s.network

    # ---- state ------------------------------------------------------------
    async def refresh_balances(self) -> None:
        info = await self.net.account_info(self.address)
        if info is None:
            raise RuntimeError(f"{self.name} wallet {self.address} is not funded/activated")
        self.xrp_balance = drops_to_xrp(info["Balance"])
        self.owner_count = int(info.get("OwnerCount", 0))
        self.tokens = await self.net.token_balances(self.address)
        self._last_refresh = time.time()

    def spendable(self, snap: MarketSnapshot) -> Decimal:
        reserve = required_reserve(snap.reserve_base_xrp, snap.reserve_inc_xrp, self.owner_count)
        free = self.xrp_balance - reserve - Decimal("0.5")
        return max(ZERO, min(free, self.s.working_balance_xrp, self.s.max_trade_xrp))

    def record_balance(self, snap: MarketSnapshot):
        v = value_holdings(self.xrp_balance, self.tokens, snap)
        self.db.balance(
            network=self.network,
            bot=self.name,
            xrp=self.xrp_balance,
            tokens_value_xrp=sum(v.tokens_value_xrp.values(), ZERO),
            total_value_xrp=v.total,
            holdings={a.key: str(x) for a, x in v.tokens.items()},
        )
        return v

    def halted(self) -> Optional[str]:
        if self.db.get_flag("kill") == "1":
            return "kill switch is on"
        flag = self.db.get_flag(f"halt:{self.name}")
        if flag:
            return flag
        return self.halted_reason

    # ---- retry cooldown ------------------------------------------------------
    def route_cooldown(self, route_key: str) -> Tuple[int, float]:
        """(failures in a row, seconds left) for a route; (0, 0) when free."""
        n, until = self._route_fails.get(route_key, (0, 0.0))
        return n, max(0.0, until - time.time())

    def note_route_result(self, route_key: str, ok: bool) -> None:
        if ok:
            self._route_fails.pop(route_key, None)
            return
        n = self._route_fails.get(route_key, (0, 0.0))[0] + 1
        wait = min(MAX_ROUTE_COOLDOWN_S, self.s.route_retry_cooldown_seconds * 2 ** (n - 1))
        self._route_fails[route_key] = (n, time.time() + wait)

    # ---- logging helpers ----------------------------------------------------
    def log_opp(self, opp: Opportunity, action: str, reason: str) -> None:
        # Don't flood the log with the same gap every 4 seconds: log it again
        # only if the reason changes or the profit moves by more than 20%.
        prev = self._last_missed.get(opp.route.key)
        if action == "missed" and prev and prev[0] == reason:
            p = prev[1]
            if p != 0 and abs((opp.net_profit - p) / p) < Decimal("0.2"):
                return
        self._last_missed[opp.route.key] = (reason, opp.net_profit)
        self.db.opportunity(
            network=self.network,
            bot=self.name,
            route=opp.route.key,
            hops=opp.route.hops,
            size_xrp=opp.size_xrp,
            gross_profit_xrp=opp.gross_profit,
            network_fee_xrp=opp.network_fee_xrp,
            net_profit_xrp=opp.net_profit,
            safe_profit_xrp=opp.safe_profit,
            action=action,
            reason=reason,
            ledger_index=opp.ledger_index,
        )

    def log_trade(self, opp: Optional[Opportunity], res: ExecResult, route_key: str) -> None:
        if opp is None:
            # Converting leftovers to XRP isn't a trade for win/loss purposes;
            # its only cost is the fee. (Real P&L comes from wallet valuations.)
            status = "conversion" if res.ok else "conversion-failed"
            profit = -res.network_fee_xrp
        elif res.ok:
            status = "win" if res.profit > 0 else "loss"
            profit = res.profit
        elif res.legs:  # two-step that needed recovery
            status = "loss" if res.profit <= 0 else "win"
            profit = res.profit
        else:
            status = "failed"
            profit = -res.network_fee_xrp
        tid = self.db.trade(
            network=self.network,
            bot=self.name,
            route=route_key,
            mode=res.mode,
            tx_hash=res.tx_hash,
            ledger_index=res.ledger_index,
            input_xrp=res.xrp_in,
            output_xrp=res.xrp_out,
            network_fee_xrp=res.network_fee_xrp,
            profit_xrp=profit,
            expected_profit_xrp=opp.safe_profit if opp else ZERO,
            result_code=res.result_code,
            status=status,
            details={"note": res.note, "leftover": {a.key: str(v) for a, v in res.leftover.items()}},
        )
        for leg in res.legs:
            self.db.leg(
                trade_id=tid,
                network=self.network,
                bot=self.name,
                tx_hash=leg.tx_hash,
                sent_asset=leg.sent_asset,
                sent_amount=leg.sent_amount,
                received_asset=leg.received_asset,
                received_amount=leg.received_amount,
                fee_xrp=ZERO,
                label=leg.label,
            )
        if res.network_fee_xrp > 0:
            self.db.leg(
                trade_id=tid,
                network=self.network,
                bot=self.name,
                tx_hash=res.tx_hash,
                sent_asset="",
                sent_amount=ZERO,
                received_asset="",
                received_amount=ZERO,
                fee_xrp=res.network_fee_xrp,
                label="network fee",
            )

    # ---- main step -----------------------------------------------------------
    async def step(self, snap: MarketSnapshot) -> None:
        if self.busy:
            return
        self.busy = True
        try:
            await self._step(snap)
        finally:
            self.busy = False

    async def _step(self, snap: MarketSnapshot) -> None:
        if time.time() - self._last_refresh > 30:
            await self.refresh_balances()
            await self.check_risk(snap)
        halted = self.halted()
        self.db.heartbeat(self.name, "halted" if halted else "running", halted or "")

        units = ATOMIC_FEE_UNITS if self.atomic else TWO_STEP_FEE_UNITS
        # In two-step mode a price move between the steps means a real loss
        # (not just a cancelled transaction), so demand a bigger margin there.
        buffer = self.s.slippage_buffer if self.atomic else max(self.s.slippage_buffer, Decimal("0.003"))
        opps = scan(
            self.routes,
            snap,
            max_size=self.spendable(snap),
            min_size=self.s.min_trade_xrp,
            min_profit=self.s.min_profit_xrp,
            slippage_buffer=buffer,
            max_fee_drops=self.s.max_network_fee_drops,
            fee_units=units,
        )
        if not opps:
            return

        chosen: Optional[Opportunity] = None
        for opp in opps:
            if not opp.executable:
                self.log_opp(opp, "missed", opp.reason)
                continue
            if halted:
                self.log_opp(opp, "missed", f"bot not trading: {halted}")
                continue
            if chosen is not None:
                self.log_opp(opp, "missed", "bot was busy with a better loop in this ledger")
                continue
            if not self.atomic and not self.s.allow_non_atomic:
                self.log_opp(
                    opp, "missed", "all-or-nothing (Batch) not available on this network and two-step mode is off"
                )
                continue
            n_fail, wait = self.route_cooldown(opp.route.key)
            if wait > 0:
                self.log_opp(
                    opp, "missed",
                    f"route paused after {n_fail} failed attempt{'s' if n_fail > 1 else ''} in a row (retry in {wait:.0f}s)",
                )
                continue
            if snap.price_age() > self.s.max_price_age_seconds:
                self.log_opp(
                    opp, "missed",
                    f"prices were {snap.price_age():.0f}s old (limit {self.s.max_price_age_seconds:.0f}s) - never trade on stale prices",
                )
                continue
            if snap.age() > self.s.scan_interval_seconds * 2:
                self.log_opp(opp, "missed", "market data too old by the time the bot was free")
                continue
            owner = self.locks.try_acquire(self.name, opp.route.edge_keys())
            if owner:
                self.log_opp(opp, "missed", f"market in use by {owner} at that moment")
                continue
            chosen = opp

        if chosen is None:
            return
        try:
            await self.execute(chosen, snap)
        finally:
            self.locks.release(self.name)

    async def execute(self, opp: Opportunity, snap: MarketSnapshot) -> None:
        # Last check right before sending: prices must still be fresh.
        if snap.price_age() > self.s.max_price_age_seconds:
            self.log_opp(opp, "missed", f"prices went stale ({snap.price_age():.0f}s old) just before sending")
            return
        self.log_opp(opp, "executed", "profitable after all costs")
        if self.atomic:
            res = await self.ex.execute_atomic(opp, fee_drops(snap, ATOMIC_FEE_UNITS))
        else:
            res = await self.ex.execute_two_step(opp, fee_drops(snap, 1), self.recover)
        self.log_trade(opp, res, opp.route.key)
        self.note_route_result(opp.route.key, res.ok)
        log.info(
            "%s %s %s result=%s profit=%s", self.name, res.mode, opp.route.label, res.result_code, res.profit
        )
        if res.leftover:
            await self.alerter.send(
                f"{self.name}: recovery could not sell everything",
                "Still holding: " + ", ".join(f"{v} {a.short}" for a, v in res.leftover.items())
                + ". The bot has stopped itself. Check the dashboard.",
                force=True,
            )
            self.halt("recovery left tokens unsold")
        await self.refresh_balances()
        await self.check_risk(snap)

    # ---- recovery & liquidation -------------------------------------------
    async def recover(self, asset: Asset, amount: Decimal) -> ExecResult:
        """Sell a token we got stuck holding back to XRP right away."""
        return await self.liquidate(asset, amount, self.s.kill_max_slippage, label="recovery")

    async def liquidate(self, asset: Asset, amount: Decimal, max_slippage: Decimal, label: str) -> ExecResult:
        steps = [s for s in LIQUIDATION_STEPS if s < max_slippage] + [max_slippage]
        total = ExecResult(ok=False, mode=label)
        remaining = amount
        for slip in steps:
            snap = await self.feed.snapshot([asset], exclude_owner=self.address)
            edge = snap.edge(asset, XRP)
            if edge is None or not edge.has_liquidity():
                total.note = "no buyers for this token right now"
                break
            expected = swap(edge, remaining).amount_out
            if expected <= 0:
                total.note = "no buyers for this token right now"
                break
            r = await self.ex.sell_to_xrp(
                asset, remaining, expected, expected * (1 - slip), fee_drops(snap, 1)
            )
            total.legs.extend(r.legs)
            total.network_fee_xrp += r.network_fee_xrp
            total.xrp_out += r.xrp_out
            total.tx_hash = total.tx_hash or r.tx_hash
            total.result_code = r.result_code
            remaining = r.leftover.get(asset, ZERO) if r.leftover else ZERO
            if remaining <= 0:
                total.ok = True
                break
        if remaining > 0:
            total.leftover = {asset: remaining}
        return total

    async def liquidate_all(self, reason: str) -> List[str]:
        """Convert every token in the wallet back to XRP (kill switch / loss cap)."""
        await self.refresh_balances()
        notes = []
        for asset, amt in list(self.tokens.items()):
            if amt <= 0:
                continue
            res = await self.liquidate(asset, amt, self.s.kill_max_slippage, label="liquidation")
            self.log_trade(None, res, f"liquidate {asset.key} ({reason})")
            if res.ok:
                notes.append(f"{asset.short}: sold for {res.xrp_out:.6f} XRP")
            else:
                left = res.leftover.get(asset, amt)
                notes.append(f"{asset.short}: COULD NOT SELL {left} ({res.note or res.result_code})")
        await self.refresh_balances()
        return notes

    async def check_wallet_exclusive(self) -> None:
        """Stop if another installation has sent a transaction from this
        wallet since we started (see instance.py)."""
        if self.halted_reason:
            return
        foreign = await check_wallet(self.net, self.address, self._started)
        if foreign:
            who = ", ".join(sorted({t["by"] for t in foreign}))
            self.halt(f"wallet used by another installation ({who})")
            await self.alerter.send(
                f"{self.name} STOPPED: its wallet is being used somewhere else",
                f"Transactions from {who} appeared on {self.address}. Only one place may run these wallets. "
                "Stop the other copy, then press Resume.",
                force=True,
            )

    async def cancel_stray_offers(self, snap: MarketSnapshot) -> int:
        """The bots trade with immediate payments and never leave standing
        orders. Any order sitting in the wallet (left over from a crash, made by
        hand, or expired) ties up reserve XRP, so cancel it once it has expired
        or been seen for longer than stray_offer_max_age_seconds."""
        offers = await self.net.account_offers(self.address)
        now = time.time()
        live = {int(o["seq"]) for o in offers}
        self._offer_seen = {q: t for q, t in self._offer_seen.items() if q in live}
        cancelled = 0
        for o in offers:
            seq = int(o["seq"])
            first = self._offer_seen.setdefault(seq, now)
            exp = o.get("expiration")
            expired = exp is not None and int(exp) + RIPPLE_EPOCH <= now
            if not expired and now - first < self.s.stray_offer_max_age_seconds:
                continue
            r = await self.ex.cancel_offer(seq, fee_drops(snap, 1))
            code = r.get("meta", {}).get("TransactionResult", "")
            fee = drops_to_xrp(_tx_fields(r).get("Fee", "0"))
            why = "expired" if expired else f"open for {now - first:.0f}s"
            self.db.event(self.network, self.name, "INFO" if code == "tesSUCCESS" else "WARN", "order-cancel",
                          f"cancelled stray order #{seq} ({why}): {code}")
            if fee > 0:
                self.db.trade(
                    network=self.network, bot=self.name, route=f"cancel order #{seq}", mode="order-cancel",
                    tx_hash=r.get("hash", ""), ledger_index=r.get("ledger_index"), input_xrp=ZERO, output_xrp=ZERO,
                    network_fee_xrp=fee, profit_xrp=-fee, expected_profit_xrp=ZERO, result_code=code,
                    status="order-cancel", details={"note": why},
                )
            if code == "tesSUCCESS":
                cancelled += 1
                self._offer_seen.pop(seq, None)
        return cancelled

    async def sweep_dust(self, snap: MarketSnapshot) -> None:
        """Small leftovers from rounding get sold once they're worth selling."""
        v = value_holdings(self.xrp_balance, self.tokens, snap)
        for asset, val in v.tokens_value_xrp.items():
            if val >= self.s.dust_sweep_min_xrp:
                res = await self.liquidate(asset, v.tokens[asset], Decimal("0.02"), label="dust-sweep")
                self.log_trade(None, res, f"dust-sweep {asset.key}")

    # ---- limits ----------------------------------------------------------------
    def halt(self, reason: str) -> None:
        self.halted_reason = reason
        self.db.set_flag(f"halt:{self.name}", reason)
        self.db.event(self.network, self.name, "WARN", "halt", reason)

    async def check_risk(self, snap: MarketSnapshot) -> None:
        v = self.record_balance(snap)
        loss, frac, start = daily_loss(self.db, self.network, self.name, v.total)
        if start is not None and loss_cap_hit(frac, self.s.daily_loss_limit_pct) and not self.halted():
            self.halt(f"daily loss cap hit ({frac * 100:.2f}% of {start:.2f} XRP)")
            notes = await self.liquidate_all("loss cap")
            await self.alerter.send(
                f"{self.name} hit its daily loss cap and stopped",
                f"Down {loss:.4f} XRP ({frac * 100:.2f}%) today. Tokens converted to XRP:\n" + "\n".join(notes)
                + "\nIt stays off until you press Resume on the dashboard.",
                force=True,
            )

    async def maybe_sweep(self, snap: MarketSnapshot) -> None:
        amt = await sweep_excess(
            self.net,
            self.wallet,
            self.s.working_balance_xrp,
            snap.reserve_base_xrp,
            snap.reserve_inc_xrp,
            self.s.vault_address,
            self.s.sweep_margin_xrp,
            fee_drops(snap, 1),
        )
        if amt:
            self.db.transfer(
                network=self.network, bot=self.name, direction="out", amount_xrp=amt, tx_hash="", note="sweep to vault"
            )
            self.db.event(self.network, self.name, "INFO", "sweep", f"moved {amt} XRP above the cap to the vault")
            await self.refresh_balances()
