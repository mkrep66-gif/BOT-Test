"""Live feed: pool balances from transaction metadata, changed books, staleness."""

import asyncio
import time
from decimal import Decimal as D

from xrplarb.amounts import XRP, Asset
from xrplarb.livefeed import LiveFeed, PairState, apply_pool_change, book_changes, pool_changes, same_amount

POOL = "rPooLaccount11111111111111111"
ISS = "rIssuer111111111111111111111"
TOK = Asset("FUZ", ISS)
PAIR = ("XRP", TOK.key) if "XRP" <= TOK.key else (TOK.key, "XRP")


def swap_meta(xrp_drops="770000000000", low=POOL, high=ISS, value="-10500.5", flags_changed=False):
    rs = {"LedgerEntryType": "RippleState",
          "FinalFields": {"Balance": {"currency": "FUZ", "issuer": "rrrrrrrrrrrrrrrrrrrrBZbvji", "value": value},
                          "LowLimit": {"issuer": low, "currency": "FUZ", "value": "0"},
                          "HighLimit": {"issuer": high, "currency": "FUZ", "value": "0"}},
          "PreviousFields": {"Flags": 0} if flags_changed else {"Balance": {"value": "0"}}}
    return {"TransactionIndex": 5, "TransactionResult": "tesSUCCESS", "AffectedNodes": [
        {"ModifiedNode": {"LedgerEntryType": "AccountRoot", "FinalFields": {"Account": POOL, "Balance": xrp_drops}}},
        {"ModifiedNode": rs},
        {"ModifiedNode": {"LedgerEntryType": "AccountRoot", "FinalFields": {"Account": "rSomeoneElse", "Balance": "5"}}},
    ]}


def test_pool_balances_come_from_metadata_with_the_right_sign():
    # Balance is from the low account's side: positive = low holds high's tokens.
    # pool is the LOW account: its holding shows as a positive balance
    changes, reread = pool_changes(swap_meta(value="10500.5"), {POOL: PAIR})
    assert not reread
    assert changes[POOL]["XRP"] == "770000000000"
    assert changes[POOL][TOK.key] == {"currency": "FUZ", "issuer": ISS, "value": "10500.5"}
    # pool is the HIGH account: holding shows as a negative low-side balance
    changes, _ = pool_changes(swap_meta(low=ISS, high=POOL, value="-10500.5"), {POOL: PAIR})
    assert D(changes[POOL][TOK.key]["value"]) == D("10500.5")


def test_freeze_or_fee_change_or_deletion_forces_a_full_reread():
    _, reread = pool_changes(swap_meta(flags_changed=True), {POOL: PAIR})
    assert POOL in reread
    amm_mod = {"AffectedNodes": [{"ModifiedNode": {"LedgerEntryType": "AMM", "FinalFields": {"Account": POOL}}}]}
    assert POOL in pool_changes(amm_mod, {POOL: PAIR})[1]
    gone = {"AffectedNodes": [{"DeletedNode": {"LedgerEntryType": "AccountRoot", "FinalFields": {"Account": POOL}}}]}
    assert POOL in pool_changes(gone, {POOL: PAIR})[1]


def test_book_changes_name_the_book():
    meta = {"AffectedNodes": [{"CreatedNode": {"LedgerEntryType": "Offer", "NewFields": {
        "TakerGets": "1000000", "TakerPays": {"currency": "FUZ", "issuer": ISS, "value": "3"}}}}]}
    assert book_changes(meta) == {("XRP", TOK.key)}


def test_apply_and_compare_amounts():
    amm = {"amount": "1", "amount2": {"currency": "FUZ", "issuer": ISS, "value": "2"}}
    apply_pool_change(amm, {"XRP": "5", TOK.key: {"currency": "FUZ", "issuer": ISS, "value": "0.0000001"}})
    assert amm["amount"] == "5"
    assert same_amount(amm["amount2"], {"currency": "FUZ", "issuer": ISS, "value": "1E-7"})
    assert not same_amount("5", "6")


def _feed_with_pool():
    feed = LiveFeed(net=None, ws_urls=[])
    feed.configure([TOK])
    st = feed.pairs[PAIR]
    st.amm = {"account": POOL, "trading_fee": 500, "amount": "1000000000",
              "amount2": {"currency": "FUZ", "issuer": ISS, "value": "1000"}}
    st.amm_known, st.amm_at, st.full_read_at = True, (100, 10**9), time.time()
    st.books_at = {("XRP", TOK.key): 100, (TOK.key, "XRP"): 100}
    st.books = {("XRP", TOK.key): [], (TOK.key, "XRP"): []}
    feed.pool_of[POOL] = PAIR
    feed._rates[ISS] = (time.time(), D(1))
    return feed, st


def test_stream_updates_pool_and_ignores_transactions_already_included():
    feed, st = _feed_with_pool()
    feed._on_tx({"validated": True, "ledger_index": 100, "meta": swap_meta(xrp_drops="1")})
    assert st.amm["amount"] == "1000000000"     # ledger 100 already in our copy
    feed._on_tx({"validated": True, "ledger_index": 101, "meta": swap_meta(xrp_drops="2000000000", value="500")})
    assert st.amm["amount"] == "2000000000" and st.amm["amount2"]["value"] == "500"
    snap = feed._build(101, time.time())
    e = snap.edge(XRP, TOK)
    assert e.amm.pool_in == D(2000) and e.amm.pool_out == D(500)


def test_pairs_we_cannot_vouch_for_are_left_out():
    feed, st = _feed_with_pool()
    assert feed._build(101, time.time()).edge(XRP, TOK) is not None
    st.dirty_books[("XRP", TOK.key)] = 101          # changed book not re-read yet
    assert feed._build(101, time.time()).edge(XRP, TOK) is None
    st.dirty_books.clear()
    st.full_read_at = time.time() - 61              # no full read for over 60 s
    assert feed._build(101, time.time()).edge(XRP, TOK) is None
    st.full_read_at = time.time()
    feed._rates.clear()                             # issuer fee unknown
    assert feed._build(101, time.time()).edge(XRP, TOK) is None


def test_unhealthy_feed_returns_none_so_the_caller_falls_back():
    feed = LiveFeed(net=None, ws_urls=[])
    assert asyncio.run(feed.next_snapshot(0.1)) is None


def test_servers_that_refused_us_go_to_the_back():
    feed = LiveFeed(net=None, ws_urls=["wss://a", "wss://b", "wss://c"])
    feed._refused["wss://a"] = time.time()
    order = feed._order([("wss://a", 0.0), ("wss://b", 0.4), ("wss://c", None)])
    assert [u for u, _ in order] == ["wss://b", "wss://c", "wss://a"]
    feed._refused["wss://a"] = time.time() - 7 * 3600  # long ago: fastest again
    assert feed._order([("wss://a", 0.0), ("wss://b", 0.4)])[0][0] == "wss://a"
