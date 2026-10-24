"""Checking paper gaps against the ledger: classifying what happened next."""

from xrplarb.amounts import XRP, Asset
from xrplarb.paperverify import classify, route_markets, tx_markets

A = Asset("AAA", "rIssuerA111111111111111111111")
B = Asset("BBB", "rIssuerB111111111111111111111")
ROUTE = f"XRP>{A.key}>{B.key}>XRP"
POOL_A, POOL_B = "rPoolA", "rPoolB"


def _pools():
    ms = route_markets(ROUTE)
    by_pair = {frozenset(m): m for m in ms}
    return {POOL_A: by_pair[frozenset(("XRP", A.key))], POOL_B: by_pair[frozenset(("XRP", B.key))]}


def _tx(*accounts, result="tesSUCCESS", who="rRival"):
    nodes = [{"ModifiedNode": {"LedgerEntryType": "AccountRoot", "FinalFields": {"Account": a, "Balance": "1"}}} for a in accounts]
    return {"Account": who, "meta": {"TransactionResult": result, "AffectedNodes": nodes}}


def test_arbitrage_through_two_of_the_loops_markets():
    v, who = classify(route_markets(ROUTE), [_tx(POOL_A, POOL_B)], _pools())
    assert v == "arbitrage" and who == "rRival"


def test_single_trade_failed_tx_and_untouched_markets():
    ms, pools = route_markets(ROUTE), _pools()
    assert classify(ms, [_tx(POOL_A)], pools)[0] == "trade"
    assert classify(ms, [_tx(POOL_A, POOL_B, result="tecPATH_PARTIAL")], pools)[0] == "none"
    assert classify(ms, [_tx("rSomeoneElse")], pools)[0] == "none"


def test_order_book_trades_count_too():
    meta = {"AffectedNodes": [{"DeletedNode": {"LedgerEntryType": "Offer", "FinalFields": {
        "TakerGets": "1000", "TakerPays": {"currency": "AAA", "issuer": A.issuer, "value": "1"}}}}]}
    assert tx_markets(meta, {}) == {tuple(sorted(("XRP", A.key)))}


def test_backrun_chances_checked_against_the_ledger(tmp_path, monkeypatch):
    import asyncio

    import xrplarb.paperverify as pv
    from xrplarb.db import DB, now_iso

    from xrplarb.localtime import today_local

    db = DB(tmp_path / "v.db")
    day = today_local()  # rows stamped "now" fall inside today's local day
    for li, trig in ((200, "T1"), (300, "T2"), (400, "T3")):
        db._insert("backrun_paper", dict(ts=now_iso(), network="mainnet-backrun", ledger_target=li, trigger_hash=trig,
                                         route=ROUTE, hops=3, size_xrp="5", safe_profit_xrp="0.1", would_trade=1, decided_ms=300))

    def tx(h, idx, *accounts, who="rOther"):
        nodes = [{"ModifiedNode": {"LedgerEntryType": "AccountRoot", "FinalFields": {"Account": a, "Balance": "1"}}} for a in accounts]
        return {"hash": h, "tx_json": {"Account": who}, "meta": {"TransactionResult": "tesSUCCESS", "TransactionIndex": idx, "AffectedNodes": nodes}}

    ledgers = {
        200: [tx("T1", 3, POOL_A), tx("X", 5, POOL_A, POOL_B, who="rRival")],   # someone backran it
        300: [tx("X0", 1, POOL_A, POOL_B), tx("T2", 4, POOL_A)],                # arb before the trade only: left open
        400: [], 401: [],                                                       # trade never landed
    }

    async def fake_pools(net, markets):
        return _pools()

    async def fake_ledger(net, index, cache):
        return ledgers.get(index, [])

    monkeypatch.setattr(pv, "_pool_accounts", fake_pools)
    monkeypatch.setattr(pv, "_ledger_txs", fake_ledger)
    out = {r["ledger"]: r for r in asyncio.run(pv.verify_backruns(db, None, "mainnet", day))}
    assert out[200]["verdict"] == "taken by another bot" and out[200]["who"] == "rRival"
    assert out[300]["verdict"] == "left open"
    assert out[400]["verdict"] == "trigger didn't land"
