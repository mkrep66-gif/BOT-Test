"""Paper backrunning: spotting the gap a pending trade will open."""

import asyncio
import time
from decimal import Decimal as D

from xrplarb.amounts import XRP, Asset
from xrplarb.backrun import PaperBackrunner, touched_markets, tx_assets
from xrplarb.config import Settings
from xrplarb.livefeed import LiveFeed
from xrplarb.routes import build_routes

A = Asset("AAA", "rIssuerA111111111111111111111")
B = Asset("BBB", "rIssuerB111111111111111111111")


def _pool(a, b, va, vb, acct):
    def amt(asset, v):
        return str(int(v * 1_000_000)) if asset.is_xrp else {"currency": asset.currency, "issuer": asset.issuer, "value": str(v)}
    return {"account": acct, "trading_fee": 300, "amount": amt(a, va), "amount2": amt(b, vb)}


def test_pending_trade_markets():
    tx = {"TransactionType": "Payment", "Amount": {"currency": "AAA", "issuer": A.issuer, "value": "5"}, "SendMax": "1000000"}
    assert tx_assets(tx) == {A, XRP}
    m = {tuple(sorted(("XRP", A.key))), tuple(sorted(("XRP", B.key))), tuple(sorted((A.key, B.key)))}
    assert touched_markets(tx, m) == {tuple(sorted(("XRP", A.key)))}
    assert touched_markets({"TransactionType": "Payment", "Amount": "5"}, m) == set()   # plain XRP payment


def test_gap_opened_by_a_pending_trade_is_logged(tmp_path):
    from xrplarb.db import DB

    db = DB(tmp_path / "b.db")
    live = LiveFeed(net=None, ws_urls=[])
    live.configure([A, B], [(A, B)])
    now = time.time()
    pools = {
        tuple(sorted(("XRP", A.key))): _pool(XRP, A, 10000, 10000, "rP1"),
        tuple(sorted(("XRP", B.key))): _pool(XRP, B, 10000, 10000, "rP2"),
        tuple(sorted((A.key, B.key))): _pool(A, B, 10000, 10000, "rP3"),
    }
    for key, st in live.pairs.items():
        st.amm, st.amm_known, st.amm_at, st.full_read_at = pools[key], True, (100, 10**9), now
        st.books_at = {(st.a.key, st.b.key): 100, (st.b.key, st.a.key): 100}
        st.books = {(st.a.key, st.b.key): [], (st.b.key, st.a.key): []}
        live.pool_of[pools[key]["account"]] = key
    for iss in (A.issuer, B.issuer):
        live._rates[iss] = (now, D(1))
    live.latest_snapshot = live._build(100, now)
    live.connected = live.synced = True
    live._last_msg = now
    assert live.healthy
    routes = build_routes(live.latest_snapshot.pairs(), 3, 3)
    bot = PaperBackrunner(live, db, Settings(network="mainnet"), "mainnet", {A.key, B.key}, lambda: routes)

    # a big pending buy of AAA for XRP: in the open ledger the XRP/AAA pool is now 13000 XRP / 7700 AAA
    async def fake_request(command, **kw):
        assert kw["ledger_index"] == "current"
        return {"amm": _pool(XRP, A, 13000, 7700, "rP1")}
    bot._request = fake_request
    tx = {"TransactionType": "Payment", "hash": "H1", "Account": "rWhale", "SendMax": "3000000000",
          "Amount": {"currency": "AAA", "issuer": A.issuer, "value": "2300"}}

    async def go():
        bot._on_pending({"type": "transaction", "validated": False, "engine_result": "tesSUCCESS",
                         "ledger_current_index": 101, "transaction": tx})
        await asyncio.sleep(0.2)
    asyncio.run(go())
    rows = db.query("SELECT * FROM backrun_paper")
    assert rows and rows[0]["ledger_target"] == 101 and rows[0]["trigger_hash"] == "H1" and rows[0]["would_trade"] == 1
    assert D(rows[0]["safe_profit_xrp"]) > D("0.02")
    assert bot.stats["chances"] >= 1 and bot.stats["read_errors"] == 0
