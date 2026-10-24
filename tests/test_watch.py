"""Watch-only mode against a simulated ledger: market reading, episode
tracking and the report."""

import asyncio
import importlib
from decimal import Decimal as D

import pytest

from xrplarb.amounts import XRP, Asset

IA, IB = "rhub8VRN55s94qWKDv6jmDy1pUykJzF3wq", "rvYAfWj5gh67oV6fW32ZzP3Aw4Eubs59B"
A, B = Asset("USD", IA), Asset("EUR", IB)


def amt(asset, v):
    return str(int(D(v) * 1_000_000)) if asset.is_xrp else {"currency": asset.currency, "issuer": asset.issuer, "value": str(v)}


class FakeLedger:
    """Serves server_info / account_info / book_offers / amm_info from pools.
    The A/B pool is mispriced for the first `gap_scans` snapshots."""

    def __init__(self, gap_scans):
        self.gap_scans = gap_scans
        self.n = 0
        self.urls = ["fake"]

    async def server_state(self):
        self.n += 1
        return {"ledger_index": 1000 + self.n, "base_fee_drops": 10, "load_factor": D(1),
                "reserve_base_xrp": D(1), "reserve_inc_xrp": D("0.2"), "network_id": 0}

    async def account_info(self, addr):
        return {"Flags": 0, "Balance": "1000000000"}

    async def book_offers(self, gets, pays, limit=30):
        return []

    async def amm_info(self, a, b):
        skew = D("0.03") if self.n <= self.gap_scans else D(0)
        pools = {
            frozenset((XRP.key, A.key)): {XRP.key: D(10000), A.key: D(5000)},
            frozenset((A.key, B.key)): {A.key: D(5000), B.key: D(4500) * (1 + skew)},
            frozenset((B.key, XRP.key)): {B.key: D(4500), XRP.key: D(10000)},
        }
        p = pools.get(frozenset((a.key, b.key)))
        if not p:
            return None
        return {"amount": amt(a, p[a.key]), "amount2": amt(b, p[b.key]), "trading_fee": 300, "account": "rAMM"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("XRPLARB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("XRPLARB_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("XRPLARB_REPORTS_DIR", str(tmp_path / "reports"))
    (tmp_path / "config").mkdir()
    import xrplarb.config as config

    importlib.reload(config)
    for m in ("xrplarb.db", "xrplarb.watch"):
        importlib.reload(importlib.import_module(m))
    config.save_tokens("mainnet", [
        {"name": "USD", "currency": "USD", "issuer": IA, "approved": False, "verdict": "OK"},
        {"name": "EUR", "currency": "EUR", "issuer": IB, "approved": False, "verdict": "CAUTION"},
    ])
    config.save_token_pairs("mainnet", [(A, B)])
    return tmp_path


def test_watch_records_episodes_and_reports(env, monkeypatch):
    import xrplarb.watch as watch
    from xrplarb.config import Settings
    from xrplarb.db import DB

    ledger = FakeLedger(gap_scans=3)
    monkeypatch.setattr(watch, "Net", lambda *a, **k: ledger)
    s = Settings(network="mainnet", scan_interval_seconds=0)
    db = DB()
    asyncio.run(watch.run_watch(s, db, max_scans=6))

    eps = db.query("SELECT * FROM watch_episodes")
    # the gap on XRP>USD>EUR>XRP lasted 3 scans -> exactly one episode for that route
    fwd = [e for e in eps if e["route"] == f"XRP>{A.key}>{B.key}>XRP"]
    assert len(fwd) == 1 and fwd[0]["scans"] == 3 and fwd[0]["open"] == 0
    assert D(fwd[0]["max_safe_profit_xrp"]) > 0 and fwd[0]["all_tokens_pass"] == 1
    assert len(db.query("SELECT * FROM watch_scans")) == 6

    p = watch.watch_report(db, "mainnet", env / "reports")
    text = p.read_text()
    assert "lasted long enough" in text and "UPPER BOUND" in text
    assert (env / "reports" / "watch_episodes_mainnet.csv").exists()


def test_daily_paper_report_counts_trades_speed_misses_and_unapproved(tmp_path):
    from decimal import Decimal as D

    from xrplarb import localtime
    from xrplarb.db import DB
    from xrplarb.watch import daily_report

    localtime.set_timezone("America/New_York")
    try:
        db = DB(tmp_path / "w.db")
        route = "XRP>AAA.rA>BBB.rB>XRP"

        def ep(scans, would, first, mx="0.5", ts="2026-07-01T15:00:00.000000Z", ledger=None):
            ep.n = getattr(ep, "n", 0) + 1
            db._insert("watch_episodes", dict(network="mainnet-watch", route=route, hops=3, first_seen=ts, last_seen=ts,
                                              ledger_first=ledger or 100 + ep.n,
                                              scans=scans, max_net_profit_xrp=mx, max_safe_profit_xrp=mx,
                                              best_size_xrp="10", all_tokens_pass=1, open=0,
                                              first_safe_profit_xrp=first, would_trade=would))

        ep(3, 1, "0.03")            # caught: counts at first-seen profit, not the 0.5 best
        ep(2, 1, "0.02", ledger=500)  # caught
        ep(2, 1, "0.01", ledger=500)  # another loop in the same burst: counts once, at the best (0.02)
        ep(1, 1, "0.04")            # gone by next scan -> missed on speed
        ep(5, 0, "0.07")            # unapproved token -> reported separately
        ep(500, 1, "0.9")           # stuck open for ages -> excluded
        ep(3, 1, "0.05", ts="2026-07-02T15:00:00.000000Z")  # next day
        full, short = daily_report(db, "mainnet", "2026-07-01")
        assert "**Would-be trades:** 2 (from 3 profitable loops" in full and "+0.0500 XRP" in full
        assert "(speed):** 1 (1 loops), worth +0.0400" in full
        assert "unapproved) tokens we'd have caught: 1 (1 loops), worth +0.0700" in full
        assert "suspiciously long" in full and "2 would-be trades" in short
    finally:
        localtime.set_timezone("")


def test_daily_report_is_sent_once_per_day(tmp_path, monkeypatch):
    import asyncio

    import xrplarb.watch as w
    from xrplarb.db import DB

    db = DB(tmp_path / "w.db")
    sent = []

    class A:
        async def send(self, subject, body="", **kw):
            sent.append(body)

    monkeypatch.setattr(w, "DAILY_SEND_HOUR", 0)
    from datetime import date, timedelta

    from xrplarb.localtime import day_start_utc

    yday = (date.today() - timedelta(days=1)).isoformat()
    db._insert("watch_scans", dict(ts=day_start_utc(yday) + ".5Z", network="mainnet-watch", ledger_index=1,
                                   routes_checked=1, profitable=0, duration_ms=1))
    monkeypatch.setattr(w, "REPORTS_DIR", tmp_path)
    for _ in range(3):
        asyncio.run(w.maybe_send_daily(db, "mainnet", A()))
    assert len(sent) == 1 and "would-be trades" in sent[0]


def test_telegram_paper_command_summarises_today(env):
    from xrplarb.db import DB
    from xrplarb.supervisor import paper_summary

    out = paper_summary(DB())
    assert out.startswith("Paper trading today") and "Would-be trades: 0" in out and "**" not in out


def test_dashboard_shows_paper_trading(env, monkeypatch):
    import xrplarb.watch as watch
    from xrplarb.config import Settings
    from xrplarb.dashboard import render_paper
    from xrplarb.db import DB

    db = DB()
    assert render_paper(db) == ""                    # never ran here: no section at all
    monkeypatch.setattr(watch, "Net", lambda *a, **k: FakeLedger(gap_scans=3))
    asyncio.run(watch.run_watch(Settings(network="mainnet", scan_interval_seconds=0), db, max_scans=6))
    db.execute("UPDATE watch_episodes SET would_trade=1")
    out = render_paper(db)
    assert "Paper trading (mainnet" in out and "Would-be trades today" in out
    assert "→ XRP" in out and "3 ledgers" in out      # the would-be trade is listed


def test_live_view_paper_mode_data(env, monkeypatch):
    import xrplarb.watch as watch
    from xrplarb.config import Settings
    from xrplarb.db import DB
    from xrplarb.liveview import live_data

    db = DB()
    monkeypatch.setattr(watch, "Net", lambda *a, **k: FakeLedger(gap_scans=3))
    asyncio.run(watch.run_watch(Settings(network="mainnet", scan_interval_seconds=0), db, max_scans=6))
    db.execute("UPDATE watch_episodes SET would_trade=1")
    d = live_data(db, "testnet")
    assert d["paper"]["shown"] and d["paper"]["trades"] >= 1
    assert d["paper_feed"] and {x["status"] for x in d["paper_feed"]} <= {"win", "missed"}
    assert d["paper_tokens"] and d["paper_tokens"][0]["trades"] >= 1
    assert all(x["kind"] == "testnet" for x in d["feed"])   # the two modes never mix


def test_telegram_health_command():
    import asyncio
    import sys

    from xrplarb.health import server_health

    out = asyncio.run(server_health(0.2))
    if sys.platform.startswith("linux"):
        assert "CPU in use now" in out and "Memory:" in out and "Disk:" in out and "Paper trading" in out


def test_telegram_commands_list():
    from xrplarb.supervisor import TELEGRAM_MENU, commands_text

    out = commands_text()
    for k, _ in TELEGRAM_MENU:
        assert f"/{k} - " in out
        assert k.islower() and k.isalnum()           # Telegram's rule for menu commands
    assert "xrplcommands" in out
