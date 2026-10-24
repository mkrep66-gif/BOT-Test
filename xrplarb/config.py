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
        # Price reads are spread across all of these.
        "rpc": [
            "https://s.altnet.rippletest.net:51234/",
            "https://testnet.xrpl-labs.com/",
            "https://clio.altnet.rippletest.net:51234/",
        ],
        # live "ledger closed" feeds (WebSocket), first that answers is used
        "ws": ["wss://testnet.xrpl-labs.com/", "wss://s.altnet.rippletest.net:51233/"],
        "faucet": "faucet.altnet.rippletest.net",
        "explorer": "https://testnet.xrpl.org/transactions/",
    },
    "mainnet": {
        "rpc": [
            "https://xrplcluster.com/",
            "https://s1.ripple.com:51234/",
            "https://s2.ripple.com:51234/",
        ],
        # Ripple's own servers first: they accept the live feed's many subscriptions;
        # xrplcluster often answers "tooBusy" to them.
        "ws": ["wss://s1.ripple.com/", "wss://s2.ripple.com/", "wss://xrplcluster.com/"],
        "faucet": None,
        "explorer": "https://livenet.xrpl.org/transactions/",
    },
}


def D(v: Any) -> Decimal:
    return Decimal(str(v))


# Display names for the bots (Live page, Telegram). Wallets, history and
# settings stay filed under bot1..bot5.
BOT_NICKNAMES = {"bot1": "QUASAR", "bot2": "VORTEX", "bot3": "GLORP", "bot4": "NEBULA-9", "bot5": "ZAPPO"}


def bot_label(bot: str) -> str:
    """'QUASAR (bot1)' - or just the name if it has no nickname."""
    nick = BOT_NICKNAMES.get(bot)
    return f"{nick} ({bot})" if nick else bot


@dataclass
class Settings:
    network: str = "testnet"
    rpc_urls: List[str] = field(default_factory=list)
    ws_urls: List[str] = field(default_factory=list)
    use_ledger_stream: bool = True
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
    requests_per_second_per_server: float = 8.0
    max_price_age_seconds: float = 60.0
    route_retry_cooldown_seconds: float = 30.0
    stray_offer_max_age_seconds: float = 120.0
    timezone: str = ""
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
    s.ws_urls = (raw.get("ws_urls") or {}).get(network) or NETWORKS[network]["ws"]
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
        "requests_per_second_per_server": float,
        "max_price_age_seconds": float,
        "route_retry_cooldown_seconds": float,
        "stray_offer_max_age_seconds": float,
        "timezone": str,
        "use_ledger_stream": bool,
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
    if not 0 < s.max_price_age_seconds <= 60:
        raise ValueError("max_price_age_seconds must be between 0 and 60 (your rule: never trade on prices older than 60 s)")
    if not 0 < s.requests_per_second_per_server <= 20:
        raise ValueError("requests_per_second_per_server must be between 0 and 20 (be kind to public servers)")
    if s.timezone:
        from zoneinfo import ZoneInfo

        ZoneInfo(s.timezone)  # raises if the name is wrong
    from .localtime import set_timezone

    set_timezone(s.timezone)
    return s


@dataclass
class ApprovedToken:
    asset: Asset
    name: str
    approved: bool
    approved_on: Optional[str]
    snapshot: Dict[str, Any]


# ---- block list -------------------------------------------------------------
# config/blocked_tokens.yaml: tokens (or whole issuers) that must never be
# traded, on any network, whatever their approval says.

def blocklist_path() -> Path:
    return CONFIG_DIR / "blocked_tokens.yaml"


def load_blocklist() -> List[Dict[str, Any]]:
    p = blocklist_path()
    if not p.exists():
        return []
    raw = yaml.safe_load(p.read_text()) or {}
    return [
        e for e in (raw.get("blocked") or [])
        if isinstance(e, dict) and (e.get("token") or e.get("issuer") or e.get("symbol"))
    ]


def save_blocklist(entries: List[Dict[str, Any]]) -> None:
    header = (
        "# Tokens the bots must NEVER trade, on any network - even if approved.\n"
        "# Add one with:   python -m xrplarb tokens-block CURRENCY.ISSUER --reason \"...\"\n"
        "# or block every token from an issuer:   python -m xrplarb tokens-block rISSUER\n"
        "# or block a name from ANY issuer (copycats too):   python -m xrplarb tokens-block NAME\n"
        "# Running bots pick up changes within a few seconds.\n"
    )
    blocklist_path().write_text(header + yaml.safe_dump({"blocked": entries}, sort_keys=False))


def blocked_reason(asset: Asset, entries: Optional[List[Dict[str, Any]]] = None) -> Optional[str]:
    """Why this token is blocked, or None. Matches CURRENCY.ISSUER (the
    currency as stored or as its readable symbol), a blocked issuer, or a
    blocked name from any issuer (case-insensitive, so look-alikes such as
    "bchamp" are caught too)."""
    if asset.is_xrp:
        return None
    for e in load_blocklist() if entries is None else entries:
        reason = e.get("reason") or "on your block list"
        if e.get("issuer") and e["issuer"] == asset.issuer:
            return reason
        name = str(e.get("symbol") or "").strip().upper()
        if name and name in (asset.symbol.strip().upper(), asset.currency.upper()):
            return reason
        tok = str(e.get("token") or "")
        if "." in tok:
            cur, iss = tok.split(".", 1)
            if iss == asset.issuer and cur in (asset.currency, asset.symbol):
                return reason
    return None


def tokens_path(network: str) -> Path:
    return CONFIG_DIR / f"approved_tokens.{network}.yaml"


def load_tokens(network: str, only_approved: bool = True) -> List[ApprovedToken]:
    p = tokens_path(network)
    if not p.exists():
        return []
    raw = yaml.safe_load(p.read_text()) or {}
    out = []
    blocked = load_blocklist()
    for t in raw.get("tokens", []) or []:
        if blocked_reason(Asset(t["currency"], t["issuer"]), blocked):
            continue
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
