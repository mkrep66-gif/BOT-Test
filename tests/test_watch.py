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
