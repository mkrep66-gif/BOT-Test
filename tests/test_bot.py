"""Bot behaviour against a simulated ledger (no network)."""

import asyncio
import importlib
from decimal import Decimal as D

import pytest

from xrplarb.amounts import XRP, Asset
from xrplarb.executor import ExecResult, LegRecord
from xrplarb.market import MarketSnapshot
from xrplarb.quote import AmmPool, Edge
from xrplarb.routes import Route

A, B = Asset("AAA", "rIA"), Asset("BBB", "rIB")


def snap(skew="0.03"):
    s = MarketSnapshot(ledger_index=1, taken_at=__import__("time").time())
    pools = {(XRP, A): (D(10000), D(5000)), (A, B): (D(5000), D(4500) * (1 + D(skew))), (B, XRP): (D(4500), D(10000))}
    for (x, y), (px, py) in pools.items():
        s.edges[(x.key, y.key)] = Edge(x, y, amm=AmmPool(px, py, D("0.003")))
        s.edges[(y.key, x.key)] = Edge(y, x, amm=AmmPool(py, px, D("0.003")))
    return s


class FakeNet:
    def __init__(self):
        self.xrp = D(100)
        self.tokens = {}

    async def account_info(self, addr):
        return {"Balance": str(int(self.xrp * 1_000_000)), "OwnerCount": 2}

    async def token_balances(self, addr):
        return dict(self.tokens)


class FakeWallet:
    classic_address = "rBOT"


class FakeAlerter:
    def __init__(self):
        self.sent = []

    async def send(self, subject, body="", **kw):
        self.sent.append(subject)


class FakeFeed:
    def __init__(self, s):
        self.s = s

    async def snapshot(self, tokens, pairs=(), exclude_owner=None):
        return self.s


@pytest.fixture
def mk(tmp_path, monkeypatch):
    monkeypatch.setenv("XRPLARB_DATA_DIR", str(tmp_path))
    import xrplarb.config
    import xrplarb.db

    importlib.reload(xrplarb.config)
    importlib.reload(xrplarb.db)
    from xrplarb.bot import Bot, MarketLocks
    from xrplarb.config import Settings
    from xrplarb.db import DB

    def make(atomic=True, **settings):
        s = Settings(**settings)
        db = DB(tmp_path / "t.db")
        net = FakeNet()
        bot = Bot("bot1", FakeWallet(), [Route((XRP, A, B, XRP))], s, net, db, FakeAlerter(), MarketLocks(),
                  FakeFeed(snap()), atomic)
        return bot, db, net

    return make


def run(c):
    return asyncio.run(c)


def test_executes_profitable_loop_atomically_and_logs(mk):
    bot, db, net = mk()
    calls = []

    async def fake_atomic(opp, fee):
        calls.append(opp)
        net.xrp += D("0.05")
        return ExecResult(ok=True, mode="atomic", tx_hash="H", xrp_in=opp.size_xrp, xrp_out=opp.size_xrp + D("0.05"),
                          network_fee_xrp=D("0.00005"),
                          legs=[LegRecord("H1", "XRP", opp.size_xrp, B.key, D(1)), LegRecord("H2", B.key, D(1), "XRP", opp.size_xrp + D("0.05"))])

    bot.ex.execute_atomic = fake_atomic
    run(bot.step(snap()))
    assert len(calls) == 1
    t = db.query("SELECT * FROM trades")[0]
    assert t["status"] == "win" and D(t["profit_xrp"]) == D("0.04995")
    assert len(db.query("SELECT * FROM legs")) == 3  # 2 swaps + fee row
    assert db.query("SELECT action FROM opportunities")[0]["action"] == "executed"


def test_trade_size_never_exceeds_working_balance(mk):
    bot, db, net = mk(working_balance_xrp=D(20), max_trade_xrp=D(20))
    net.xrp = D(500)
    seen = []

    async def fake_atomic(opp, fee):
        seen.append(opp.size_xrp)
        return ExecResult(ok=False, mode="atomic", result_code="batch-cancelled")

    bot.ex.execute_atomic = fake_atomic
    run(bot.step(snap()))
    assert seen and seen[0] <= D(20)


def test_no_trade_without_atomic_unless_opted_in(mk):
    bot, db, _ = mk(atomic=False)
    run(bot.step(snap()))
    assert not db.query("SELECT * FROM trades")
    r = db.query("SELECT action, reason FROM opportunities")[0]
    assert r["action"] == "missed" and "Batch" in r["reason"]


def test_kill_switch_blocks_trading(mk):
    bot, db, _ = mk()
    db.set_flag("kill", "1")
    run(bot.step(snap()))
    assert not db.query("SELECT * FROM trades")
    assert "kill switch" in db.query("SELECT reason FROM opportunities")[0]["reason"]


def test_failed_batch_costs_only_fee(mk):
    bot, db, _ = mk()

    async def fake_atomic(opp, fee):
        return ExecResult(ok=False, mode="atomic", tx_hash="H", result_code="batch-cancelled:tecPATH_PARTIAL",
                          network_fee_xrp=D("0.00005"))

    bot.ex.execute_atomic = fake_atomic
    run(bot.step(snap()))
    t = db.query("SELECT * FROM trades")[0]
    assert t["status"] == "failed" and D(t["profit_xrp"]) == D("-0.00005")


def test_two_step_recovery_sells_leftover(mk):
    bot, db, net = mk(atomic=False, allow_non_atomic=True)
    recovered = []

    async def fake_two_step(opp, fee, recover):
        r = await recover(B, D(5))
        recovered.append(r)
        return ExecResult(ok=False, mode="two-step+recovery", legs=r.legs, xrp_in=opp.size_xrp, xrp_out=r.xrp_out,
                          network_fee_xrp=D("0.00003"), leftover=r.leftover)

    async def fake_sell(asset, amount, expected, min_xrp, fee):
        assert min_xrp < expected
        return ExecResult(ok=True, mode="liquidation", xrp_out=expected,
                          legs=[LegRecord("L", asset.key, amount, "XRP", expected, "recovery")])

    bot.ex.execute_two_step = fake_two_step
    bot.ex.sell_to_xrp = fake_sell
    run(bot.step(snap()))
    assert recovered and recovered[0].ok
    assert db.query("SELECT mode FROM trades")[0]["mode"] == "two-step+recovery"


def test_loss_cap_halts_and_liquidates(mk):
    bot, db, net = mk()
    run(bot.refresh_balances())
    run(bot.check_risk(snap()))  # day-start snapshot: 100 XRP
    net.xrp = D(80)
    net.tokens = {B: D(2)}
    sold = []

    async def fake_sell(asset, amount, expected, min_xrp, fee):
        sold.append((asset, amount))
        net.tokens = {}
        return ExecResult(ok=True, mode="liquidation", xrp_out=expected)

    bot.ex.sell_to_xrp = fake_sell
    run(bot.refresh_balances())
    run(bot.check_risk(snap()))
    assert bot.halted() and "loss cap" in bot.halted()
    assert sold == [(B, D(2))]
    assert any("loss cap" in s for s in bot.alerter.sent)
    run(bot.step(snap()))
    assert all(r["action"] == "missed" for r in db.query("SELECT action FROM opportunities"))


def test_missed_gap_logging_is_deduplicated(mk):
    bot, db, _ = mk(atomic=False)
    for _ in range(5):
        run(bot.step(snap()))
    assert len(db.query("SELECT * FROM opportunities")) == 1
