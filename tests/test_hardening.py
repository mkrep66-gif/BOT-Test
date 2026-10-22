"""Server courtesy, stale prices, retry cooldown, stray orders, block list,
local time, weekly review and secret scrubbing."""

import asyncio
import importlib
import logging
import time
from decimal import Decimal as D

import pytest
from xrpl.models.requests import ServerInfo
from xrpl.models.response import Response, ResponseStatus

from xrplarb.amounts import XRP, Asset
from xrplarb.executor import ExecResult
from xrplarb.xrplnet import Net, RPCError

from test_bot import A, mk, run, snap  # noqa: F401  (fixture)


class Srv:
    def __init__(self, name, error=None):
        self.name, self.error, self.calls = name, error, []

    async def request(self, req):
        self.calls.append(time.monotonic())
        if self.error:
            return Response(status=ResponseStatus.ERROR, result={"error": self.error})
        return Response(status=ResponseStatus.SUCCESS, result={"server": self.name})


# ---- 1. be gentle with public servers ------------------------------------------

def test_requests_are_paced_not_bursted():
    net = Net(["https://one/"], rate_per_server=20)
    s = Srv("one")
    net.clients = [s]

    async def go():
        await asyncio.gather(*(net.request(ServerInfo()) for _ in range(6)))

    run(go())
    gaps = [b - a for a, b in zip(s.calls, s.calls[1:])]
    assert len(s.calls) == 6 and min(gaps) >= 0.045  # 20/s -> 50 ms apart


def test_reads_are_spread_across_servers():
    net = Net(["https://a/", "https://b/"], rate_per_server=100)
    a, b = Srv("a"), Srv("b")
    net.clients = [a, b]
    for _ in range(6):
        run(net.request(ServerInfo()))
    assert len(a.calls) == 3 and len(b.calls) == 3


def test_busy_server_is_left_alone_and_others_used():
    net = Net(["https://busy/", "https://ok/"], rate_per_server=100)
    busy, ok = Srv("busy", error="slowDown"), Srv("ok")
    net.clients = [busy, ok]
    results = [run(net.request(ServerInfo()))["server"] for _ in range(4)]
    assert results == ["ok"] * 4
    assert len(busy.calls) == 1  # tried once, then cooling down
    h = {x["url"]: x for x in net.health()}
    assert h["https://busy/"]["backoffs"] == 1 and h["https://busy/"]["cooling_down_s"] > 0


def test_backoff_grows_each_time():
    net = Net(["https://busy/"], rate_per_server=100)
    sv = net.servers[0]
    waits = []
    for _ in range(3):
        waits.append(sv.back_off("x"))
        sv.cooldown_until = 0  # the wait has passed
    assert waits == [2.0, 4.0, 8.0]
    sv.ok()
    assert sv.back_off("x") == 2.0


def test_failures_during_one_cooldown_count_once():
    sv = Net(["https://busy/"]).servers[0]
    sv.back_off("x")
    sv.back_off("x")
    sv.back_off("x")
    assert sv.strikes == 1


def test_non_busy_errors_are_not_retried():
    net = Net(["https://a/"], rate_per_server=100)
    s = Srv("a", error="actNotFound")
    net.clients = [s]
    with pytest.raises(RPCError):
        run(net.request(ServerInfo()))
    assert len(s.calls) == 1


# ---- 4. persistent connections ---------------------------------------------------

def test_http_connection_pool_is_reused_within_a_run():
    net = Net(["https://a/"])

    async def go():
        pool = net.servers[0].client._pool
        c1, c2 = pool.get(), pool.get()
        await net.close()
        return c1 is c2 and pool.get() is not c1

    assert run(go())


# ---- 2. never trade on stale prices --------------------------------------------

def test_skips_trade_when_prices_older_than_limit(mk):
    bot, db, _ = mk()
    called = []

    async def fake_atomic(opp, fee):
        called.append(opp)
        return ExecResult(ok=True, mode="atomic")

    bot.ex.execute_atomic = fake_atomic
    s = snap()
    s.ledger_age_s = 61  # ledger closed over a minute before we read it
    run(bot.step(s))
    assert not called
    r = db.query("SELECT action, reason FROM opportunities")[0]
    assert r["action"] == "missed" and "old" in r["reason"]


def test_price_age_limit_cannot_be_raised_above_60(tmp_path):
    from xrplarb.config import load_settings

    p = tmp_path / "s.yaml"
    p.write_text("max_price_age_seconds: 120\n")
    with pytest.raises(ValueError):
        load_settings(p)


# ---- 3. retry cooldown + stray orders ----------------------------------------------

def test_failed_route_cools_down_and_doubles(mk):
    bot, db, _ = mk(route_retry_cooldown_seconds=30)
    calls = []

    async def fake_atomic(opp, fee):
        calls.append(opp)
        return ExecResult(ok=False, mode="atomic", result_code="batch-cancelled")

    bot.ex.execute_atomic = fake_atomic
    run(bot.step(snap()))
    run(bot.step(snap()))
    assert len(calls) == 1  # second scan skipped: route paused
    key = calls[0].route.key
    n, wait = bot.route_cooldown(key)
    assert n == 1 and 25 < wait <= 30
    bot.note_route_result(key, ok=False)
    assert 55 < bot.route_cooldown(key)[1] <= 60
    bot.note_route_result(key, ok=True)
    assert bot.route_cooldown(key) == (0, 0.0)
    reasons = [r["reason"] for r in db.query("SELECT reason FROM opportunities WHERE action='missed'")]
    assert any(r.startswith("route paused") for r in reasons)


def test_stray_and_expired_orders_are_cancelled(mk):
    bot, db, net = mk(stray_offer_max_age_seconds=120)
    now = time.time()
    offers = [
        {"seq": 5, "expiration": int(now - 946684800 - 10)},  # already expired
        {"seq": 6},  # just appeared
    ]

    async def account_offers(addr):
        return list(offers)

    cancelled = []

    async def cancel_offer(seq, fee):
        cancelled.append(seq)
        return {"hash": f"C{seq}", "tx_json": {"Fee": "12"}, "meta": {"TransactionResult": "tesSUCCESS"}}

    net.account_offers = account_offers
    bot.ex.cancel_offer = cancel_offer
    assert run(bot.cancel_stray_offers(snap())) == 1 and cancelled == [5]
    offers.pop(0)
    bot._offer_seen[6] = now - 200  # has now been sitting there too long
    run(bot.cancel_stray_offers(snap()))
    assert cancelled == [5, 6]
    t = db.query("SELECT status, profit_xrp FROM trades")
    assert len(t) == 2 and t[0]["status"] == "order-cancel" and D(t[0]["profit_xrp"]) == D("-0.000012")


# ---- 5. block list ----------------------------------------------------------------------

@pytest.fixture
def cfgdir(tmp_path, monkeypatch):
    import xrplarb.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    return config


def test_block_list_matches_token_symbol_and_issuer(cfgdir):
    hexcode = "5553444300000000000000000000000000000000"  # 'USDC' as a 40-char code
    cfgdir.save_blocklist([
        {"token": "USDC.rIssuer1", "reason": "scam"},
        {"issuer": "rBadIssuer", "reason": "bad issuer"},
    ])
    assert cfgdir.blocked_reason(Asset(hexcode, "rIssuer1")) == "scam"
    assert cfgdir.blocked_reason(Asset("ANY", "rBadIssuer")) == "bad issuer"
    assert cfgdir.blocked_reason(Asset("USDC", "rOther")) is None
    assert cfgdir.blocked_reason(XRP) is None


def test_blocked_token_never_loaded_even_if_approved(cfgdir):
    cfgdir.save_tokens("testnet", [
        {"currency": "TUS", "issuer": "rI", "approved": True},
        {"currency": "TEU", "issuer": "rI", "approved": True},
    ])
    cfgdir.save_blocklist([{"token": "TUS.rI"}])
    assert [t.asset.currency for t in cfgdir.load_tokens("testnet")] == ["TEU"]


def test_cli_refuses_to_approve_blocked_token(cfgdir, monkeypatch):
    from xrplarb import cli

    cfgdir.save_tokens("testnet", [{"currency": "TUS", "issuer": "rI", "approved": False}])
    cfgdir.save_blocklist([{"token": "TUS.rI", "reason": "no"}])
    with pytest.raises(SystemExit) as e:
        cli.main(["tokens-approve", "TUS.rI", "--network", "testnet"])
    assert "block list" in str(e.value)
    assert cfgdir.load_token_entries("testnet")[0]["approved"] is False


# ---- 6. local time ------------------------------------------------------------------------

@pytest.fixture
def ny():
    from xrplarb import localtime

    localtime.set_timezone("America/New_York")
    yield localtime
    localtime.set_timezone("")


def test_local_midnight_boundaries(ny):
    assert ny.day_start_utc("2026-07-01") == "2026-07-01T04:00:00"  # EDT
    assert ny.day_start_utc("2026-12-01") == "2026-12-01T05:00:00"  # EST
    assert ny.fmt_local("2026-07-01T03:30:00.000000Z") == "06-30 23:30:00"


def test_profit_today_resets_at_local_midnight(ny, tmp_path):
    from xrplarb.db import DB
    from xrplarb.history import day_result
    from xrplarb.risk import daily_loss

    db = DB(tmp_path / "t.db")

    def bal(ts, v):
        db._insert("balances", dict(ts=ts, network="testnet", bot="b1", xrp=str(v), tokens_value_xrp="0", total_value_xrp=str(v)))

    bal("2026-07-01T03:00:00.000000Z", 100)  # 11pm on June 30 in New York
    bal("2026-07-01T05:00:00.000000Z", 99)   # 1am July 1
    bal("2026-07-02T03:59:00.000000Z", 98)   # 11:59pm July 1
    db._insert("trades", dict(ts="2026-07-01T03:30:00.000000Z", network="testnet", bot="b1", status="win", profit_xrp="0.5", network_fee_xrp="0.00004"))
    db._insert("trades", dict(ts="2026-07-01T04:30:00.000000Z", network="testnet", bot="b1", status="failed", profit_xrp="-0.00004", network_fee_xrp="0.00004"))

    # The 03:30 UTC win belongs to June 30 locally, the 04:30 one to July 1.
    jun30, jul1 = day_result(db, "testnet", "2026-06-30"), day_result(db, "testnet", "2026-07-01")
    assert (jun30.attempts, jun30.wins) == (1, 1)
    assert (jul1.attempts, jul1.no_fills) == (1, 1)
    assert jul1.wallet_change == D(-2)  # 100 at local midnight -> 98 at the next one
    loss, frac, start = daily_loss(db, "testnet", "b1", D(98), day="2026-07-01")
    assert start == D(100) and loss == D(2)


# ---- weekly review -----------------------------------------------------------------------------

def test_weekly_review_plain_english(tmp_path):
    from xrplarb.db import DB
    from xrplarb.history import review_markdown, weekly_review

    db = DB(tmp_path / "t.db")
    day = "2026-07-03"
    for i, (st, p, code) in enumerate([("win", "0.03", "tesSUCCESS"), ("failed", "-0.00004", "batch-cancelled"),
                                        ("failed", "-0.00004", "batch-cancelled"), ("loss", "-0.01", "tesSUCCESS")]):
        db._insert("trades", dict(ts=f"{day}T12:0{i}:00.000000Z", network="testnet", bot="b1", status=st, profit_xrp=p,
                                  network_fee_xrp="0.00004", result_code=code, expected_profit_xrp="0.02"))
    db._insert("opportunities", dict(ts=f"{day}T12:00:00.000000Z", network="testnet", bot="b1", action="missed",
                                     reason="route paused after 2 failed attempts in a row (retry in 41s)"))
    db._insert("events", dict(ts=f"{day}T13:00:00.000000Z", network="testnet", bot="b2", level="ERROR", kind="crash",
                              message="ConnectError: boom"))
    db._insert("events", dict(ts=f"{day}T14:00:00.000000Z", network="testnet", bot="system", level="INFO",
                              kind="net-health", message="https://a/ requests=100 failures=3 backoffs=2"))
    rv = weekly_review(db, "testnet", days=7, until="2026-07-05")
    assert (rv.attempts, rv.wins, rv.no_fills) == (4, 1, 2)
    assert rv.trade_profit == D("0.01992")
    md = review_markdown(rv)
    assert "**Win rate:** 25%" in md and "ConnectError" in md and "retry cooldown" in md
    assert "price moved" in md and "| https://a/ | 100 | 3 | 2 |" in md


# ---- 7. secrets never in logs -----------------------------------------------------------------

def test_tracebacks_in_logs_are_scrubbed(tmp_path):
    from xrplarb.redact import register_secrets, setup_logging

    seed = "sEdTM1uX8pu2do5XvTnutH6HsouMaM2"
    register_secrets([seed])
    logfile = tmp_path / "bots.log"
    setup_logging(logfile=str(logfile))
    try:
        raise RuntimeError(f"signing failed for {seed}")
    except RuntimeError:
        logging.getLogger("t").exception("crash")
    for h in logging.getLogger().handlers:
        h.flush()
    text = logfile.read_text()
    assert seed not in text and "REDACTED" in text
    assert oct(logfile.stat().st_mode & 0o777) == "0o600"
    setup_logging(logging.WARNING)


def test_failing_transaction_server_is_swapped_out():
    net = Net(["https://a/", "https://b/"], rate_per_server=100)
    a, b = Srv("a"), Srv("b")
    net.clients = [a, b]
    assert run(net.working_client()) is a
    run(net.report_failure(a, ConnectionError("reset")))
    assert run(net.working_client()) is b


def test_transaction_validated_at_deadline_is_not_reported_expired(monkeypatch):
    # Race seen live: tx not yet validated -> ledger passes the deadline ->
    # tx was in fact validated in the deadline ledger.
    import xrplarb.executor as ex_mod
    from xrplarb.executor import Executor

    class Signed:
        last_ledger_sequence = 10
        fee = "12"

        def get_hash(self):
            return "H"

    async def fake_sign(tx, client, wallet):
        return Signed()

    async def fake_submit(signed, client):
        return Response(status=ResponseStatus.SUCCESS, result={"engine_result": "tesSUCCESS"})

    async def latest(client):
        return 11

    async def no_sleep(_):
        pass

    lookups = []

    class Client:
        async def request(self, req):
            lookups.append(1)
            done = len(lookups) > 1
            return Response(status=ResponseStatus.SUCCESS,
                            result={"validated": done, "meta": {"TransactionResult": "tesSUCCESS"}})

    monkeypatch.setattr(ex_mod, "autofill_and_sign", fake_sign)
    monkeypatch.setattr(ex_mod, "submit", fake_submit)
    monkeypatch.setattr(ex_mod, "get_latest_validated_ledger_sequence", latest)
    monkeypatch.setattr(ex_mod.asyncio, "sleep", no_sleep)

    class W:
        classic_address = "rBOT"

    r = run(Executor(None, W())._submit_on(Client(), object()))
    assert r["meta"]["TransactionResult"] == "tesSUCCESS"
