from decimal import Decimal as D

from xrplarb.amounts import XRP, Asset
from xrplarb.market import MarketSnapshot
from xrplarb.quote import AmmPool, Edge
from xrplarb.routes import Route, build_routes, partition_routes
from xrplarb.scanner import evaluate

A, B, C, E = (Asset(c, "rI" + c) for c in ("AAA", "BBB", "CCC", "EEE"))


def full_pairs(tokens):
    pairs = []
    nodes = [XRP] + tokens
    for x in nodes:
        for y in nodes:
            if x != y:
                pairs.append((x, y))
    return pairs


def test_routes_start_end_xrp_and_respect_hops():
    routes = build_routes(full_pairs([A, B, C, E]), 3, 5)
    assert routes
    for r in routes:
        assert r.assets[0] == XRP and r.assets[-1] == XRP
        assert 3 <= r.hops <= 5
        assert len(set(r.tokens)) == len(r.tokens)
    # 4 tokens: ordered selections of 2,3,4 tokens = 12 + 24 + 24
    assert len(routes) == 60


def test_no_route_without_markets():
    pairs = [(XRP, A), (A, XRP), (XRP, B), (B, XRP)]  # no A<->B market
    assert build_routes(pairs, 3, 5) == []


def test_partition_is_disjoint_complete_and_groups_token_sets():
    routes = build_routes(full_pairs([A, B, C, E]), 3, 5)
    parts = partition_routes(routes, 5)
    flat = [r for p in parts for r in p]
    assert sorted(r.key for r in flat) == sorted(r.key for r in routes)
    owner = {}
    for i, p in enumerate(parts):
        for r in p:
            assert owner.setdefault(r.token_set_key, i) == i
    assert parts == partition_routes(routes, 5)  # deterministic


def test_route_key_roundtrip():
    r = Route((XRP, A, B, XRP))
    assert Route.from_key(r.key) == r


def _snap(skew):
    s = MarketSnapshot(ledger_index=1, taken_at=0)
    pools = {
        (XRP, A): (D(10000), D(5000)),
        (A, B): (D(5000), D(4500) * (1 + skew)),
        (B, XRP): (D(4500), D(10000)),
    }
    for (x, y), (px, py) in pools.items():
        s.edges[(x.key, y.key)] = Edge(x, y, amm=AmmPool(px, py, D("0.003")))
        s.edges[(y.key, x.key)] = Edge(y, x, amm=AmmPool(py, px, D("0.003")))
    return s


KW = dict(max_size=D(100), min_size=D(1), min_profit=D("0.02"), slippage_buffer=D("0.003"), max_fee_drops=2000)


def test_evaluate_executable_includes_all_costs():
    r = Route((XRP, A, B, XRP))
    o = evaluate(r, _snap(D("0.03")), **KW)
    assert o.executable
    assert o.safe_profit < o.net_profit < o.gross_profit
    assert o.xrp_floor == o.size_xrp + o.network_fee_xrp + D("0.02")
    assert o.last_token_target < o.quote.leg_outputs[-2]


def test_evaluate_none_when_no_gap():
    assert evaluate(Route((XRP, A, B, XRP)), _snap(D(0)), **KW) is None


def test_small_gap_is_logged_with_reason_not_traded():
    o = evaluate(Route((XRP, A, B, XRP)), _snap(D("0.012")), **KW)
    assert o is not None and not o.executable and o.reason


def test_fee_spike_blocks_trade():
    s = _snap(D("0.03"))
    s.load_factor = D(1000)
    o = evaluate(Route((XRP, A, B, XRP)), s, **KW)
    assert not o.executable and "network fees too high" in o.reason


def test_tighter_buffer_makes_marginal_gap_tradeable():
    kw = dict(KW, slippage_buffer=D("0.0005"))
    o = evaluate(Route((XRP, A, B, XRP)), _snap(D("0.015")), **kw)
    assert o.executable and o.safe_profit >= D("0.02")
