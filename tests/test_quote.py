from decimal import Decimal as D

from xrplarb.amounts import XRP, Asset
from xrplarb.quote import AmmPool, Edge, Offer, best_size, loop_marginal_rate, quote_loop, swap

USD = Asset("USD", "rIssuerUSD")
EUR = Asset("EUR", "rIssuerEUR")


def test_amm_constant_product_with_fee():
    pool = AmmPool(pool_in=D(1000), pool_out=D(500), fee=D("0.003"))
    out = pool.out_for(D(10))
    eff = D(10) * D("0.997")
    assert out == D(500) * eff / (D(1000) + eff)
    assert out < D(5)  # worse than spot because of fee + price impact


def test_book_walk_uses_best_prices_first_and_stops_at_depth():
    e = Edge(XRP, USD, offers=[Offer(out_amount=D(10), in_amount=D(20)), Offer(out_amount=D(10), in_amount=D(25))])
    r = swap(e, D(30))
    # 20 XRP buys 10 USD at 2.0; next 10 XRP buys 4 USD at 2.5
    assert r.amount_out == D(14)


def test_transfer_fee_is_charged_on_input():
    e = Edge(USD, XRP, offers=[Offer(out_amount=D(1000), in_amount=D(500))], transfer_rate_in=D("1.01"))
    r = swap(e, D("101"))
    assert r.transfer_fee_paid == D("101") - D("101") / D("1.01")
    assert abs(r.amount_out - D(200)) < D("0.0000001")


def test_mixed_book_and_amm_beats_either_alone():
    amm = AmmPool(pool_in=D(1000), pool_out=D(500), fee=D("0.003"))
    offers = [Offer(out_amount=D(5), in_amount=D("9.9"))]
    mixed = swap(Edge(XRP, USD, offers=offers, amm=amm), D(30)).amount_out
    amm_only = swap(Edge(XRP, USD, amm=amm), D(30)).amount_out
    assert mixed > amm_only


def _loop(skew: D):
    # XRP->USD->EUR->XRP with a mispriced USD/EUR pool
    return [
        Edge(XRP, USD, amm=AmmPool(D(10000), D(5000), D("0.003"))),
        Edge(USD, EUR, amm=AmmPool(D(5000), D(4500) * (1 + skew), D("0.003"))),
        Edge(EUR, XRP, amm=AmmPool(D(4500), D(10000), D("0.003"))),
    ]


def test_fair_loop_has_no_profit():
    edges = _loop(D(0))
    assert loop_marginal_rate(edges) < 1  # fees alone make a fair loop lose
    assert best_size(edges, D(1), D(100), D("0.00004")).net_profit < 0


def test_mispriced_loop_is_found_and_sized():
    edges = _loop(D("0.03"))
    assert loop_marginal_rate(edges) > 1
    s = best_size(edges, D(1), D(100), D("0.00004"))
    assert s.net_profit > 0
    # larger isn't always better: check the chosen size beats both ends
    for size in (D(1), D(100)):
        q = quote_loop(edges, size)
        assert s.net_profit >= q.amount_out - size - D("0.00004") - D("0.000001")


def test_thin_book_loop_marked_unfilled():
    edges = [
        Edge(XRP, USD, offers=[Offer(D(1), D(2))]),
        Edge(USD, XRP, offers=[Offer(D(100), D(1))]),
    ]
    assert not quote_loop(edges, D(10)).filled


def test_token_held_between_the_two_payments_pays_its_transfer_fee_twice():
    # Verified against the ledger's own simulation: XRP -> TGB (0.2% fee)
    # delivers 1/1.002 of the pool output into our wallet, and selling it
    # back costs the fee again.
    from decimal import Decimal as D

    from xrplarb.amounts import XRP, Asset
    from xrplarb.quote import AmmPool, Edge, quote_loop, swap

    A, G = Asset("AAA", "rA"), Asset("TGB", "rG")
    e1 = Edge(XRP, A, amm=AmmPool(D(100), D(100), D("0.003")))
    e2 = Edge(A, G, amm=AmmPool(D(100), D(100), D("0.003")))
    e3 = Edge(G, XRP, amm=AmmPool(D(100), D(100), D("0.003")), transfer_rate_in=D("1.002"))
    q = quote_loop([e1, e2, e3], D(1))
    pool_out = swap(e2, swap(e1, D(1)).amount_out).amount_out
    assert q.leg_outputs[1] == pool_out / D("1.002")
    assert q.amount_out == swap(e3, q.leg_outputs[1]).amount_out
