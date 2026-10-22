from decimal import Decimal as D

from xrpl.core.binarycodec import encode
from xrpl.models.transactions import Batch

from xrplarb.amounts import XRP, Asset, make_amount, round_sig
from xrplarb.executor import build_atomic_batch, legs_from_meta
from xrplarb.market import amm_to_pool, offers_to_edge_offers
from xrplarb.routes import Route
from xrplarb.scanner import Opportunity

ME = "rPEPPER7kfTD9w2To4CQk6UCfuHM9c6GDY"
ISS_A = "rhub8VRN55s94qWKDv6jmDy1pUykJzF3wq"
ISS_B = "rvYAfWj5gh67oV6fW32ZzP3Aw4Eubs59B"
A, B = Asset("USD", ISS_A), Asset("EUR", ISS_B)


def _opp():
    o = Opportunity(route=Route((XRP, A, B, XRP)), edges=[], ledger_index=1)
    o.size_xrp = D("10")
    o.last_token_target = D("4.123456789012345678")
    o.xrp_floor = D("10.02004")
    return o


def test_batch_is_all_or_nothing_with_two_self_payments():
    b = build_atomic_batch(ME, _opp(), fee_drops=48, last_ledger=100)
    d = b.to_xrpl()
    assert d["TransactionType"] == "Batch"
    assert d["Flags"] & 0x00010000  # tfAllOrNothing
    inner = [r["RawTransaction"] for r in d["RawTransactions"]]
    assert len(inner) == 2
    p1, p2 = inner
    for p in inner:
        assert p["Account"] == ME and p["Destination"] == ME
        assert p["Fee"] == "0" and p["SigningPubKey"] == ""
        assert p["Flags"] & 0x40000000  # inner batch flag
    assert p1["SendMax"] == "10000000"  # never spend more than the planned size
    assert p1["Amount"]["currency"] == "EUR"
    assert p1["Paths"] == [[{"currency": "USD", "issuer": ISS_A}]]
    assert p2["Flags"] & 0x00020000  # partial payment: sell all of it...
    assert p2["DeliverMin"] == "10020040"  # ...but never for less than the floor
    assert p2["SendMax"]["value"] == p1["Amount"]["value"]
    # 15 significant digits, rounded down
    assert p1["Amount"]["value"] == "4.12345678901234"
    # serialises (sequence/fee filled like autofill would)
    d2 = Batch.from_xrpl({**d, "Sequence": 5}).to_xrpl()
    assert encode(d2)


def test_amount_rounding_never_rounds_up_what_we_promise():
    assert make_amount(XRP, D("1.0000019")) == "1000001"
    assert round_sig(D("123456789.123456789"), 15) == D("123456789.123456")


def test_book_offers_parse_uses_funded_amounts_and_sorts():
    raw = [
        {"Account": "rX", "TakerGets": {"currency": "USD", "issuer": ISS_A, "value": "10"}, "TakerPays": "25000000"},
        {"Account": "rY", "TakerGets": {"currency": "USD", "issuer": ISS_A, "value": "10"}, "TakerPays": "20000000",
         "taker_gets_funded": {"currency": "USD", "issuer": ISS_A, "value": "5"}, "taker_pays_funded": "10000000"},
        {"Account": ME, "TakerGets": {"currency": "USD", "issuer": ISS_A, "value": "100"}, "TakerPays": "1000000"},
    ]
    offers = offers_to_edge_offers(raw, exclude_owner=ME)
    assert [o.owner for o in offers] == ["rY", "rX"]
    assert offers[0].out_amount == D(5) and offers[0].in_amount == D(10)


def test_amm_orientation():
    amm = {"amount": "1000000000", "amount2": {"currency": "USD", "issuer": ISS_A, "value": "500"}, "trading_fee": 300}
    p = amm_to_pool(amm, XRP)
    assert p.pool_in == D(1000) and p.pool_out == D(500) and p.fee == D("0.003")
    p2 = amm_to_pool(amm, A)
    assert p2.pool_in == D(500) and p2.pool_out == D(1000)
    assert amm_to_pool({**amm, "asset_frozen": True}, XRP) is None


def _acct_node(addr, before, after):
    return {"ModifiedNode": {"LedgerEntryType": "AccountRoot", "LedgerIndex": "A1", "FinalFields": {"Account": addr, "Balance": after},
                             "PreviousFields": {"Balance": before}}}


def _line_node(holder, issuer, cur, before, after):
    # balance from the low account's perspective; make holder the low account
    return {"ModifiedNode": {"LedgerEntryType": "RippleState", "LedgerIndex": "B2",
                             "FinalFields": {"Balance": {"currency": cur, "issuer": "rrrrrrrrrrrrrrrrrrrrBZbvji", "value": after},
                                             "LowLimit": {"currency": cur, "issuer": holder, "value": "1e15"},
                                             "HighLimit": {"currency": cur, "issuer": issuer, "value": "0"}},
                             "PreviousFields": {"Balance": {"currency": cur, "issuer": "rrrrrrrrrrrrrrrrrrrrBZbvji", "value": before}}}}


def test_legs_from_meta_sent_and_received_excluding_fee():
    meta = {"TransactionResult": "tesSUCCESS", "AffectedNodes": [
        _acct_node(ME, "100000000", "89999988"),  # -10 XRP and -12 drops fee
        _line_node(ME, ISS_B, "EUR", "0", "4.5"),
    ]}
    legs = legs_from_meta(ME, "H", meta, fee_xrp=D("0.000012"))
    assert len(legs) == 1
    l = legs[0]
    assert l.sent_asset == "XRP" and l.sent_amount == D(10)
    assert l.received_asset == f"EUR.{ISS_B}" and l.received_amount == D("4.5")


def test_inner_results_reads_parent_batch_id_from_meta():
    # Shape returned by a live Testnet server (api_version 1): ParentBatchID is in meta.
    import asyncio

    from xrplarb.executor import Executor

    class FakeNet:
        async def request(self, req):
            return {
                "transactions": [
                    {"tx": {"TransactionType": "Batch", "hash": "BB"}, "meta": {"TransactionResult": "tesSUCCESS"}},
                    {"tx": {"TransactionType": "Payment", "hash": "P1"},
                     "meta": {"TransactionResult": "tesSUCCESS", "ParentBatchID": "bb"}},
                    {"tx": {"TransactionType": "Payment", "hash": "P2"},
                     "meta": {"TransactionResult": "tesSUCCESS", "ParentBatchID": "BB"}},
                    {"tx": {"TransactionType": "Payment", "hash": "X"},
                     "meta": {"TransactionResult": "tesSUCCESS", "ParentBatchID": "OTHER"}},
                ]
            }

    class FakeWallet:
        classic_address = ME

    ex = Executor(FakeNet(), FakeWallet())
    found = asyncio.run(ex._inner_results("BB", 5))
    assert [f["hash"] for f in found] == ["P1", "P2"]


def test_working_client_skips_unreachable_server():
    import asyncio

    from xrpl.models.response import Response, ResponseStatus

    from xrplarb.xrplnet import Net

    class Down:
        async def request(self, req):
            raise ConnectionError("blocked port")

    class Up:
        async def request(self, req):
            return Response(status=ResponseStatus.SUCCESS, result={"info": {}})

    net = Net(["https://down:51234/", "https://up/"])
    up = Up()
    net.clients = [Down(), up]
    assert asyncio.run(net.working_client()) is up


def test_amm_info_request_builds_and_reads_validated_ledger():
    # xrpl-py's AMMInfo model rejects ledger_index; this used to raise TypeError
    # and every pool was silently dropped as a fetch error.
    import asyncio

    from xrplarb.xrplnet import Net

    sent = []
    net = Net(["https://x/"])

    async def fake_request(req, retries=3):
        sent.append(req.to_dict())
        return {"amm": {"amount": "1"}}

    net.request = fake_request
    assert asyncio.run(net.amm_info(XRP, A)) == {"amount": "1"}
    assert sent[0]["method"] == "amm_info" and sent[0]["ledger_index"] == "validated"
    assert sent[0]["asset"] == {"currency": "XRP"} and sent[0]["asset2"] == {"currency": "USD", "issuer": ISS_A}


def test_first_payment_uses_only_our_route():
    # Without tfNoRippleDirect the ledger mixes in its own direct route and
    # an exact-amount first payment can fail (seen on Testnet).
    from xrpl.models.transactions import PaymentFlag

    from xrplarb.executor import build_payments

    p1, p2 = build_payments(ME, _opp(), inner=False)
    assert p1.has_flag(PaymentFlag.TF_NO_RIPPLE_DIRECT) and p1.paths
    assert p2.has_flag(PaymentFlag.TF_PARTIAL_PAYMENT)
    b = build_atomic_batch(ME, _opp(), 40)
    assert all(t.has_flag(PaymentFlag.TF_NO_RIPPLE_DIRECT) for t in b.raw_transactions[:1])
