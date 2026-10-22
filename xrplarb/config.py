"""Loads the non-secret settings (config/settings.yaml) and the approved token list."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .amounts import Asset

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = Path(os.environ.get("XRPLARB_CONFIG_DIR", ROOT / "config"))
DATA_DIR = Path(os.environ.get("XRPLARB_DATA_DIR", ROOT / "data"))
SECRETS_DIR = Path(os.environ.get("XRPLARB_SECRETS_DIR", ROOT / "secrets"))
REPORTS_DIR = Path(os.environ.get("XRPLARB_REPORTS_DIR", ROOT / "reports"))

NETWORKS = {
    "testnet": {
        # Ripple's servers use port 51234; xrpl-labs is on the standard port 443
        # (helps when a firewall only allows normal HTTPS).
        "rpc": ["https://s.altnet.rippletest.net:51234/", "https://testnet.xrpl-labs.com/"],
        "faucet": "faucet.altnet.rippletest.net",
        "explorer": "https://testnet.xrpl.org/transactions/",
    },
    "mainnet": {
        "rpc": [
            "https://xrplcluster.com/",
            "https://s1.ripple.com:51234/",
            "https://s2.ripple.com:51234/",
        ],
        "faucet": None,
        "explorer": "https://livenet.xrpl.org/transactions/",
    },
}


def D(v: Any) -> Decimal:
    return Decimal(str(v))


@dataclass
class Settings:
    network: str = "testnet"
    rpc_urls: List[str] = field(default_factory=list)
    allow_mainnet_trading: bool = False
    num_bots: int = 5
    working_balance_xrp: Decimal = D(100)
    max_trade_xrp: Decimal = D(100)
    min_trade_xrp: Decimal = D(1)
    min_hops: int = 3
    max_hops: int = 5
    min_profit_xrp: Decimal = D("0.02")
    slippage_buffer: Decimal = D("0.0005")
    max_network_fee_drops: int = 2000
    scan_interval_seconds: float = 4.0
    book_depth: int = 30
    allow_non_atomic: bool = False
    non_atomic_max_loss_pct: Decimal = D("0.02")
    daily_loss_limit_pct: Decimal = D("0.10")
    kill_max_slippage: Decimal = D("0.25")
    dust_sweep_min_xrp: Decimal = D("0.05")
    vault_address: Optional[str] = None
    sweep_margin_xrp: Decimal = D(1)
    batch_amendment_names: List[str] = field(default_factory=lambda: ["BatchV1_1"])
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8080
    watch_min_profit_xrp: Decimal = D("0.0")
    watch_max_size_xrp: Decimal = D(100)
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def explorer(self) -> str:
        return NETWORKS[self.network]["explorer"]


def load_settings(path: Optional[Path] = None, network_override: Optional[str] = None) -> Settings:
    path = path or CONFIG_DIR / "settings.yaml"
    raw: Dict[str, Any] = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text()) or {}
    s = Settings(raw=raw)
    network = network_override or raw.get("network", "testnet")
    if network not in NETWORKS:
        raise ValueError(f"Unknown network {network!r}")
    s.network = network
    s.rpc_urls = raw.get("rpc_urls", {}).get(network) or NETWORKS[network]["rpc"]
    simple = {
        "allow_mainnet_trading": bool,
        "num_bots": int,
        "working_balance_xrp": D,
        "max_trade_xrp": D,
        "min_trade_xrp": D,
        "min_hops": int,
        "max_hops": int,
        "min_profit_xrp": D,
        "slippage_buffer": D,
        "max_network_fee_drops": int,
        "scan_interval_seconds": float,
        "book_depth": int,
        "allow_non_atomic": bool,
        "non_atomic_max_loss_pct": D,
        "daily_loss_limit_pct": D,
        "kill_max_slippage": D,
        "dust_sweep_min_xrp": D,
        "sweep_margin_xrp": D,
        "batch_amendment_names": list,
        "dashboard_host": str,
        "dashboard_port": int,
        "watch_min_profit_xrp": D,
        "watch_max_size_xrp": D,
    }
    for k, conv in simple.items():
        if k in raw and raw[k] is not None:
            setattr(s, k, conv(raw[k]))
    vault = raw.get("vault_address", {})
    if isinstance(vault, dict):
        s.vault_address = vault.get(network) or None
    elif vault:
        s.vault_address = vault
    # Hard safety rails that config cannot loosen.
    if not 1 <= s.num_bots <= 5:
        raise ValueError("num_bots must be between 1 and 5")
    if s.working_balance_xrp > 100:
        raise ValueError("working_balance_xrp may not exceed 100 XRP (your rule)")
    s.max_trade_xrp = min(s.max_trade_xrp, s.working_balance_xrp)
    if not (3 <= s.min_hops <= s.max_hops <= 5):
        raise ValueError("hops must satisfy 3 <= min_hops <= max_hops <= 5")
    if s.daily_loss_limit_pct > D("0.10"):
        raise ValueError("daily_loss_limit_pct may not exceed 10% (your rule)")
    return s


@dataclass
class ApprovedToken:
    asset: Asset
    name: str
    approved: bool
    approved_on: Optional[str]
    snapshot: Dict[str, Any]


def tokens_path(network: str) -> Path:
    return CONFIG_DIR / f"approved_tokens.{network}.yaml"


def load_tokens(network: str, only_approved: bool = True) -> List[ApprovedToken]:
    p = tokens_path(network)
    if not p.exists():
        return []
    raw = yaml.safe_load(p.read_text()) or {}
    out = []
    for t in raw.get("tokens", []) or []:
        tok = ApprovedToken(
            asset=Asset(t["currency"], t["issuer"]),
            name=t.get("name", t["currency"]),
            approved=bool(t.get("approved", False)),
            approved_on=t.get("approved_on"),
            snapshot=t.get("approval_snapshot", {}) or {},
        )
        if tok.approved or not only_approved:
            out.append(tok)
    return out


def save_tokens(network: str, entries: List[Dict[str, Any]]) -> None:
    p = tokens_path(network)
    header = (
        "# Tokens the bots may trade on this network.\n"
        "# A token is only used when `approved: true`. Approval is done with:\n"
        "#   python -m xrplarb tokens approve <CURRENCY.ISSUER> --network " + network + "\n"
        "# and only after the owner has confirmed it in writing.\n"
    )
    p.write_text(header + yaml.safe_dump({"network": network, "tokens": entries}, sort_keys=False))


def load_token_entries(network: str) -> List[Dict[str, Any]]:
    p = tokens_path(network)
    if not p.exists():
        return []
    raw = yaml.safe_load(p.read_text()) or {}
    return list(raw.get("tokens", []) or [])


def markets_path(network: str) -> Path:
    return CONFIG_DIR / f"markets.{network}.yaml"


def load_token_pairs(network: str) -> List[tuple]:
    """Token/token markets (besides each token's XRP market) to watch."""
    p = markets_path(network)
    if not p.exists():
        return []
    raw = yaml.safe_load(p.read_text()) or {}
    return [(Asset.from_key(a), Asset.from_key(b)) for a, b in raw.get("token_pairs", []) or []]


def save_token_pairs(network: str, pairs: List[tuple]) -> None:
    p = markets_path(network)
    p.write_text(
        "# Token/token markets found on the ledger (written by `discover`).\n"
        + yaml.safe_dump({"token_pairs": [[a.key, b.key] for a, b in pairs]}, sort_keys=False)
    )
