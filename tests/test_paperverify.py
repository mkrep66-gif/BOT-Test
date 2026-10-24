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
