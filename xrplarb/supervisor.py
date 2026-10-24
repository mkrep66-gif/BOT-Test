"""The supervisor runs all bots in one process and is in charge of safety:

  * refuses to trade on mainnet unless settings explicitly allow it
  * detects whether all-or-nothing (Batch) transactions are available
  * fetches market data once per cycle and hands it to every bot
  * watches the kill switch (dashboard button or Telegram /kill)
  * re-checks every approved token's issuer settings every few minutes
  * catches crashes, alerts you, restarts a crashed bot a few times, then gives up
"""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from decimal import Decimal
from typing import Dict, List, Optional

from . import secrets_store
from .alerts import Alerter
from .bot import Bot, MarketLocks
from .config import Settings, blocked_reason, bot_label, blocklist_path, load_blocklist, load_token_pairs, load_tokens
from .db import DB
from .market import MarketFeed, MarketSnapshot
from .redact import register_secrets
from .routes import build_routes, partition_routes
from .instance import LOOKBACK_S, RunLock, check_wallet, instance_id
from .ledgerstream import LedgerStream
from .tokens import TokenReport, assess_issuer
from .wallets import bot_name, load_wallet
from .xrplnet import Net

log = logging.getLogger(__name__)

MAX_CRASHES_PER_HOUR = 3
TOKEN_RECHECK_SECONDS = 300
NET_HEALTH_LOG_SECONDS = 3600
BLOCKED_TAG = "on your block list"


def paper_summary(db: DB) -> str:
    """Today's mainnet paper trading (Telegram /paper): the report's key lines."""
    from .localtime import today_local
    from .watch import daily_report

    try:
        text, _ = daily_report(db, "mainnet", today_local())
    except Exception as e:
        return f"Paper report unavailable: {e}"
    lines = [ln[2:].replace("**", "") for ln in text.splitlines() if ln.startswith("- ")]
    return "Paper trading today (mainnet, no XRP spent):\n" + "\n".join(lines)


class Supervisor:
    def __init__(self, settings: Settings, db: Optional[DB] = None):
        self.s = settings
        self.db = db or DB()
        self.net = Net(settings.rpc_urls, network=settings.network, rate_per_server=settings.requests_per_second_per_server)
        self.alerter = Alerter(settings.network, self.db)
        self.feed = MarketFeed(self.net, depth=settings.book_depth)
        self.locks = MarketLocks()
        self.bots: List[Bot] = []
        self.atomic = False
        self.tokens = []
        self.token_pairs = []
        self._stop = asyncio.Event()
        self._killed_handled = False
        self._crashes: Dict[str, List[float]] = {}
        self._disabled_tokens: Dict[str, str] = {}
        self._blocklist_mtime: Optional[float] = None
        self.stream = LedgerStream(settings.ws_urls if settings.use_ledger_stream else [])

    # ---- startup -------------------------------------------------------------
    async def setup(self) -> None:
        s = self.s
        if s.network == "mainnet" and not s.allow_mainnet_trading:
            raise SystemExit(
                "Mainnet trading is OFF (allow_mainnet_trading: false). Use `watch` for read-only mainnet scanning."
            )
        register_secrets(secrets_store.all_seeds())
        register_secrets([self.alerter.tg_token or "", (self.alerter.email or {}).get("password", "")])
        from .security import run_checks

        failed = [m for ok, m in run_checks() if not ok]
        if failed:
            msg = "Security check failed: " + "; ".join(failed)
            self.db.event(s.network, "system", "ERROR", "security", msg)
            raise SystemExit(msg + "\nRun `python -m xrplarb security-check` for details.")

        approved = load_tokens(s.network, only_approved=True)
        if not approved:
            raise SystemExit(f"No approved tokens for {s.network}. Review and approve tokens first.")
        self.tokens = [t.asset for t in approved]
        self._approval = {t.asset.key: t.snapshot for t in approved}
        tset = set(self.tokens)
        self.token_pairs = [(a, b) for a, b in load_token_pairs(s.network) if a in tset and b in tset]

        name = await self.net.amendment_enabled(s.batch_amendment_names)
        self.atomic = name is not None
        if self.atomic:
            self.db.event(s.network, "system", "INFO", "startup", f"all-or-nothing Batch available ({name})")
        else:
            msg = (
                "The ledger's all-or-nothing 'Batch' feature is NOT enabled on this network. "
                + (
                    "Two-step mode is ON: each loop is 2 transactions, and a failed second step is "
                    "recovered by selling the token back to XRP."
                    if s.allow_non_atomic
                    else "Two-step mode is OFF, so bots will scan and log but not trade."
                )
            )
            self.db.event(s.network, "system", "WARN", "startup", msg)
            await self.alerter.send("Atomic trading unavailable", msg, force=True)

        wallets = {w["name"]: w for w in secrets_store.wallets(s.network)}
        names = [bot_name(i + 1) for i in range(s.num_bots)]
        missing = [n for n in names if n not in wallets]
        if missing:
            raise SystemExit(f"Missing wallets for {missing}. Run `wallets create` first.")

        # Never trade wallets that another installation is using right now.
        since = time.time() - LOOKBACK_S
        clashes = []
        for n in names:
            for t in await check_wallet(self.net, wallets[n]["address"], since):
                clashes.append(f"{n}: transaction {t['hash'][:12]}… sent by {t['by']}")
        if clashes:
            msg = (
                f"These wallets were used by another installation in the last {LOOKBACK_S // 60} minutes "
                f"(this one is {instance_id()}). Stop the bots there first:\n" + "\n".join(clashes)
            )
            self.db.event(s.network, "system", "ERROR", "wallet-clash", msg)
            await self.alerter.send("Bots NOT started: wallets in use elsewhere", msg, force=True)
            raise SystemExit(msg)

        self.feed.our_accounts = [wallets[n]["address"] for n in names]
        snap = await self.feed.snapshot(self.tokens, self.token_pairs)
        routes = build_routes(snap.pairs(), s.min_hops, s.max_hops)
        groups = partition_routes(routes, s.num_bots)
        for n, group in zip(names, groups):
            b = Bot(n, load_wallet(s.network, n), group, s, self.net, self.db, self.alerter, self.locks, self.feed, self.atomic)
            await b.refresh_balances()
            b.record_balance(snap)
            self.bots.append(b)
        self.db.event(
            s.network, "system", "INFO", "startup",
            f"{len(self.bots)} bots, {len(self.tokens)} tokens, {len(routes)} routes "
            f"({', '.join(f'{b.name}:{len(b.routes)}' for b in self.bots)})",
        )
        if self.db.get_flag("clean_shutdown") == "0":
            await self.alerter.send("Bots restarted after an unexpected stop", "Check the dashboard events list.", force=True)
        self.db.set_flag("clean_shutdown", "0")

    async def apply_blocklist(self) -> None:
        """Pick up edits to config/blocked_tokens.yaml while running."""
        p = blocklist_path()
        mtime = p.stat().st_mtime if p.exists() else 0.0
        if mtime == self._blocklist_mtime:
            return
        first = self._blocklist_mtime is None
        self._blocklist_mtime = mtime
        entries = load_blocklist()
        changed = False
        for t in self.tokens:
            why = blocked_reason(t, entries)
            current = self._disabled_tokens.get(t.key, "")
            if why and not current.startswith(BLOCKED_TAG):
                self._disabled_tokens[t.key] = f"{BLOCKED_TAG}: {why}"
                changed = True
                if not first:
                    self.db.event(self.s.network, "system", "WARN", "blocked", f"stopped trading {t.short}: {why}")
                    await self.alerter.send(f"Stopped trading {t.short}", f"Added to your block list: {why}", force=True)
            elif not why and current.startswith(BLOCKED_TAG):
                del self._disabled_tokens[t.key]
                changed = True
                self.db.event(self.s.network, "system", "INFO", "unblocked", f"{t.short} removed from the block list")
        if changed and self.feed.latest:
            self.rebuild_routes(self.feed.latest)

    def rebuild_routes(self, snap: MarketSnapshot) -> None:
        active = [t for t in self.tokens if t.key not in self._disabled_tokens]
        aset = set(active)
        pairs = [(a, b) for a, b in snap.pairs() if (a.is_xrp or a in aset) and (b.is_xrp or b in aset)]
        groups = partition_routes(build_routes(pairs, self.s.min_hops, self.s.max_hops), len(self.bots))
        for b, g in zip(self.bots, groups):
            b.routes = g

    # ---- loops -------------------------------------------------------------
    async def market_loop(self) -> None:
        last_routes = 0.0
        last_house = 0.0
        last_health = time.time()
        while not self._stop.is_set():
            t0 = time.time()
            if self.db.get_flag("kill") == "1":
                await asyncio.sleep(1)
                continue
            await self.apply_blocklist()
            if time.time() - last_health > NET_HEALTH_LOG_SECONDS:
                last_health = time.time()
                self.log_net_health()
            try:
                active = [t for t in self.tokens if t.key not in self._disabled_tokens]
                snap = await self.feed.snapshot(active, self.token_pairs, exclude_owner=None)
            except Exception as e:
                log.warning("market fetch failed: %s", e)
                await self.alerter.send("Market data fetch failing", str(e)[:300], key="marketfail")
                await asyncio.sleep(5)
                continue
            if time.time() - last_routes > 600:  # markets appear/disappear; refresh routes
                self.rebuild_routes(snap)
                last_routes = time.time()
            for b in self.bots:
                if not b.busy and not self._bot_dead(b):
                    asyncio.create_task(self._guarded_step(b, snap))
            if time.time() - last_house > 120:
                last_house = time.time()
                for b in self.bots:
                    if not b.busy and not self._bot_dead(b):
                        asyncio.create_task(self._housekeeping(b, snap))
            # Scan again as soon as the next ledger closes (live feed); fall back
            # to the timer if the feed is down. If a newer ledger already
            # closed while we were scanning, go straight away.
            if self.stream.latest and snap.ledger_index and self.stream.latest > snap.ledger_index:
                await asyncio.sleep(0)
            elif not await self.stream.wait_next(max(0.05, self.s.scan_interval_seconds - (time.time() - t0))):
                pass  # timed out: timer fallback
            else:
                await asyncio.sleep(0.15)  # let the closed ledger become 'validated' on our servers

    def log_net_health(self) -> None:
        """Hourly line per server for the weekly review: requests, failures and
        times we backed off because it was busy, during the past hour."""
        for h in self.net.health(reset=True):
            self.db.event(
                self.s.network, "system", "INFO", "net-health",
                f"{h['url']} requests={h['requests']} failures={h['failures']} backoffs={h['backoffs']}",
            )

    def _bot_dead(self, b: Bot) -> bool:
        return self.db.get_flag(f"dead:{b.name}") == "1"

    async def _guarded_step(self, b: Bot, snap: MarketSnapshot) -> None:
        try:
            await b.step(snap)
        except Exception as e:
            log.exception("%s crashed", b.name)
            now = time.time()
            hist = [t for t in self._crashes.get(b.name, []) if now - t < 3600] + [now]
            self._crashes[b.name] = hist
            self.db.event(self.s.network, b.name, "ERROR", "crash", f"{type(e).__name__}: {e}")
            self.db.heartbeat(b.name, "error", str(e)[:200])
            if len(hist) >= MAX_CRASHES_PER_HOUR:
                self.db.set_flag(f"dead:{b.name}", "1")
                self.db.heartbeat(b.name, "stopped", f"crashed {len(hist)} times in an hour")
                await self.alerter.send(
                    f"{b.name} STOPPED after repeated crashes",
                    f"Last error: {type(e).__name__}: {str(e)[:300]}. Press Resume on the dashboard to retry.",
                    force=True,
                )
            else:
                await self.alerter.send(f"{b.name} crashed (auto-retrying)", f"{type(e).__name__}: {str(e)[:300]}", key=f"crash:{b.name}")

    async def _housekeeping(self, b: Bot, snap: MarketSnapshot) -> None:
        b.busy = True
        try:
            await b.refresh_balances()
            await b.check_wallet_exclusive()
            await b.cancel_stray_offers(snap)
            if not b.halted():
                await b.sweep_dust(snap)
                await b.maybe_sweep(snap)
        except Exception as e:
            log.warning("housekeeping %s: %s", b.name, e)
        finally:
            b.busy = False

    async def control_loop(self) -> None:
        """Watches the kill switch and resume requests."""
        while not self._stop.is_set():
            kill = self.db.get_flag("kill") == "1"
            if kill and not self._killed_handled:
                self._killed_handled = True
                await self.handle_kill()
            elif not kill and self._killed_handled:
                self._killed_handled = False
                self.db.event(self.s.network, "system", "INFO", "resume", "kill switch released")
            if self.db.get_flag("resume") == "1":
                self.db.clear_flag("resume")
                for b in self.bots:
                    b.halted_reason = None
                    self.db.clear_flag(f"halt:{b.name}")
                    self.db.clear_flag(f"dead:{b.name}")
                    self._crashes.pop(b.name, None)
                await self.alerter.send("Bots resumed", "All bots are trading again.", force=True)
            await asyncio.sleep(1)

    async def handle_kill(self) -> None:
        await self.alerter.send("KILL SWITCH pressed", "Stopping all bots and converting everything to XRP…", force=True)
        # wait for in-flight trades (atomic ones finish within a few seconds)
        for _ in range(30):
            if not any(b.busy for b in self.bots):
                break
            await asyncio.sleep(1)
        report = []
        for b in self.bots:
            b.busy = True
            try:
                notes = await b.liquidate_all("kill switch")
                report.append(f"{b.name}: " + ("; ".join(notes) if notes else "already all XRP") + f" — {b.xrp_balance:.4f} XRP")
            except Exception as e:
                report.append(f"{b.name}: ERROR during conversion: {e}")
            finally:
                b.busy = False
        self.db.event(self.s.network, "system", "WARN", "kill", " | ".join(report))
        await self.alerter.send("All bots stopped", "\n".join(report), force=True)

    async def token_recheck_loop(self) -> None:
        """If an issuer adds a fee, enables clawback/freeze, etc. after approval,
        stop using that token straight away."""
        while not self._stop.is_set():
            await asyncio.sleep(TOKEN_RECHECK_SECONDS)
            for t in self.tokens:
                try:
                    info = await self.net.account_info(t.issuer)
                except Exception:
                    continue
                r = TokenReport(asset=t)
                assess_issuer(r, info)
                appr = self._approval.get(t.key, {})
                worse = []
                if r.global_freeze:
                    worse.append("token is globally frozen")
                if r.require_auth:
                    worse.append("issuer now requires authorization")
                if r.clawback and not appr.get("clawback"):
                    worse.append("clawback enabled")
                if r.transfer_fee_pct > Decimal(str(appr.get("transfer_fee_pct", "0"))):
                    worse.append(f"transfer fee raised to {r.transfer_fee_pct:.3f}%")
                if worse and t.key not in self._disabled_tokens:
                    self._disabled_tokens[t.key] = "; ".join(worse)
                    await self.alerter.send(
                        f"Stopped trading {t.short}", "Issuer changed settings: " + "; ".join(worse), force=True
                    )
                    if self.feed.latest:
                        self.rebuild_routes(self.feed.latest)
                elif not worse and t.key in self._disabled_tokens and not self._disabled_tokens[t.key].startswith(BLOCKED_TAG):
                    del self._disabled_tokens[t.key]

    async def deadman_loop(self) -> None:
        """Optional: ping a 'dead man's switch' service (e.g. healthchecks.io)
        every minute. If the whole server dies and the pings stop, THAT service
        emails/Telegrams you - covering the case where we can't alert ourselves."""
        url = secrets_store.get("alerts.deadman_url")
        if not url:
            return
        import httpx

        async with httpx.AsyncClient(timeout=10) as c:
            while not self._stop.is_set():
                try:
                    await c.get(url)
                except Exception as e:
                    log.warning("dead-man ping failed: %s", e)
                await asyncio.sleep(60)

    async def telegram_handler(self, text: str) -> str:
        parts = text.split()
        cmd = parts[0].lower() if parts else ""
        pin_ok = (self.alerter.kill_pin is None) or (len(parts) > 1 and parts[1] == self.alerter.kill_pin)
        if cmd == "/status":
            lines = []
            for b in self.bots:
                lines.append(f"{bot_label(b.name)}: {'HALTED - ' + b.halted() if b.halted() else 'running'}, {b.xrp_balance:.3f} XRP")
            return "\n".join(lines) or "no bots"
        if cmd == "/kill":
            if not pin_ok:
                return "Wrong or missing PIN. Use: /kill <pin>"
            self.db.set_flag("kill", "1")
            return "Kill switch ON. Converting everything to XRP…"
        if cmd == "/resume":
            if not pin_ok:
                return "Wrong or missing PIN. Use: /resume <pin>"
            self.db.clear_flag("kill")
            self.db.set_flag("resume", "1")
            return "Resuming."
        if cmd == "/paper":
            return paper_summary(self.db)
        return "Commands: /status, /paper, /kill <pin>, /resume <pin>"

    async def run(self) -> None:
        lock = RunLock(self.s.network)
        lock.acquire()
        try:
            await self._run()
        finally:
            lock.release()
            await self.net.close()

    async def _run(self) -> None:
        await self.setup()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._stop.set)
            except NotImplementedError:
                pass
        await self.alerter.send("Bots started", f"{len(self.bots)} bots on {self.s.network}", force=True)
        tasks = [
            asyncio.create_task(self.market_loop()),
            asyncio.create_task(self.stream.run()),
            asyncio.create_task(self.control_loop()),
            asyncio.create_task(self.token_recheck_loop()),
            asyncio.create_task(self.alerter.poll_commands(self.telegram_handler)),
            asyncio.create_task(self.deadman_loop()),
        ]
        stop_task = asyncio.create_task(self._stop.wait())
        pending = set(tasks) | {stop_task}
        try:
            while not self._stop.is_set():
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for t in done:
                    if t is not stop_task and not t.cancelled() and t.exception():
                        e = t.exception()
                        self.db.event(self.s.network, "system", "ERROR", "crash", f"{type(e).__name__}: {e}")
                        await self.alerter.send(
                            "Supervisor crashed - all bots stopping",
                            f"{type(e).__name__}: {str(e)[:300]}. The service manager will restart it.",
                            force=True,
                        )
                        raise e
        finally:
            for t in tasks:
                t.cancel()
            for _ in range(30):
                if not any(b.busy for b in self.bots):
                    break
                await asyncio.sleep(1)
            self.db.set_flag("clean_shutdown", "1")
            await self.alerter.send("Bots stopped (normal shutdown)", "", force=True)
