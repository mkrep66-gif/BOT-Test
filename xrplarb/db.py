"""The logbook: one SQLite file (data/xrplarb.db) holding every trade, fee,
missed opportunity, balance snapshot, alert and control flag.

SQLite is a single local file - nothing to install, easy to back up, and both
the bots and the dashboard can read it at the same time.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .config import DATA_DIR

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, network TEXT, bot TEXT,
  level TEXT, kind TEXT, message TEXT
);
CREATE TABLE IF NOT EXISTS opportunities (
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, network TEXT, bot TEXT,
  route TEXT, hops INTEGER, size_xrp TEXT, gross_profit_xrp TEXT,
  network_fee_xrp TEXT, net_profit_xrp TEXT, safe_profit_xrp TEXT,
  action TEXT, reason TEXT, ledger_index INTEGER
);
CREATE INDEX IF NOT EXISTS opp_ts ON opportunities(ts);
CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, network TEXT, bot TEXT,
  route TEXT, mode TEXT, tx_hash TEXT, ledger_index INTEGER,
  input_xrp TEXT, output_xrp TEXT, network_fee_xrp TEXT, profit_xrp TEXT,
  expected_profit_xrp TEXT, result_code TEXT, status TEXT, details TEXT
);
CREATE INDEX IF NOT EXISTS trades_ts ON trades(ts);
CREATE TABLE IF NOT EXISTS legs (
  id INTEGER PRIMARY KEY, trade_id INTEGER, ts TEXT NOT NULL, network TEXT, bot TEXT,
  tx_hash TEXT, sent_asset TEXT, sent_amount TEXT,
  received_asset TEXT, received_amount TEXT, fee_xrp TEXT, label TEXT
);
CREATE TABLE IF NOT EXISTS balances (
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, network TEXT, bot TEXT,
  xrp TEXT, tokens_value_xrp TEXT, total_value_xrp TEXT, holdings TEXT
);
CREATE TABLE IF NOT EXISTS transfers (
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, network TEXT, bot TEXT,
  direction TEXT, amount_xrp TEXT, tx_hash TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS control (
  key TEXT PRIMARY KEY, value TEXT, ts TEXT
);
CREATE TABLE IF NOT EXISTS heartbeats (
  bot TEXT PRIMARY KEY, ts TEXT, status TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS watch_episodes (
  id INTEGER PRIMARY KEY, ts TEXT, network TEXT, route TEXT, hops INTEGER,
  first_seen TEXT, last_seen TEXT, scans INTEGER,
  ledger_first INTEGER, ledger_last INTEGER,
  max_net_profit_xrp TEXT, max_safe_profit_xrp TEXT, best_size_xrp TEXT,
  all_tokens_pass INTEGER, open INTEGER
);
CREATE TABLE IF NOT EXISTS watch_scans (
  id INTEGER PRIMARY KEY, ts TEXT, network TEXT, ledger_index INTEGER,
  routes_checked INTEGER, profitable INTEGER, duration_ms INTEGER
);
"""


# Columns added after the first release (older databases get them on open).
MIGRATIONS = [
    ("watch_episodes", "first_safe_profit_xrp", "TEXT"),  # profit when the gap was first seen = when a bot would fire
    ("watch_episodes", "would_trade", "INTEGER"),  # 1 if every token on the route is approved
]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _s(v: Any) -> Any:
    if isinstance(v, Decimal):
        return str(v)
    return v


class DB:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path or DATA_DIR / "xrplarb.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        for table, col, kind in MIGRATIONS:
            try:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {kind}")
            except sqlite3.OperationalError:
                pass  # already there
        self.conn.commit()

    def _insert(self, table: str, row: Dict[str, Any]) -> int:
        row = {k: _s(v) for k, v in row.items()}
        row.setdefault("ts", now_iso())
        cols = ",".join(row)
        qs = ",".join("?" for _ in row)
        with self._lock:
            cur = self.conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({qs})", list(row.values()))
            self.conn.commit()
            return int(cur.lastrowid)

    def query(self, sql: str, args: Iterable[Any] = ()) -> List[sqlite3.Row]:
        with self._lock:
            return list(self.conn.execute(sql, list(args)))

    def execute(self, sql: str, args: Iterable[Any] = ()) -> None:
        with self._lock:
            self.conn.execute(sql, list(args))
            self.conn.commit()

    # ---- writers -----------------------------------------------------------
    def event(self, network: str, bot: str, level: str, kind: str, message: str) -> None:
        from .redact import scrub

        self._insert("events", dict(network=network, bot=bot, level=level, kind=kind, message=scrub(message)))

    def opportunity(self, **row: Any) -> int:
        return self._insert("opportunities", row)

    def trade(self, **row: Any) -> int:
        if "details" in row and not isinstance(row["details"], str):
            row["details"] = json.dumps(row["details"], default=str)
        return self._insert("trades", row)

    def leg(self, **row: Any) -> int:
        return self._insert("legs", row)

    def balance(self, **row: Any) -> int:
        if "holdings" in row and not isinstance(row["holdings"], str):
            row["holdings"] = json.dumps(row["holdings"], default=str)
        return self._insert("balances", row)

    def transfer(self, **row: Any) -> int:
        return self._insert("transfers", row)

    def heartbeat(self, bot: str, status: str, detail: str = "") -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO heartbeats(bot, ts, status, detail) VALUES(?,?,?,?) "
                "ON CONFLICT(bot) DO UPDATE SET ts=excluded.ts, status=excluded.status, detail=excluded.detail",
                (bot, now_iso(), status, detail),
            )
            self.conn.commit()

    # ---- control flags (kill switch, halts) ----------------------------------
    def set_flag(self, key: str, value: str) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO control(key, value, ts) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, ts=excluded.ts",
                (key, value, now_iso()),
            )
            self.conn.commit()

    def get_flag(self, key: str, default: Optional[str] = None) -> Optional[str]:
        rows = self.query("SELECT value FROM control WHERE key=?", (key,))
        return rows[0]["value"] if rows else default

    def clear_flag(self, key: str) -> None:
        self.execute("DELETE FROM control WHERE key=?", (key,))

    # ---- readers used by risk + dashboard ----------------------------------
    def day_start_value(self, network: str, bot: str, day: str) -> tuple[Optional[Decimal], Optional[str]]:
        """Value at the start of `day` (your local midnight): the last snapshot
        before midnight, or (for a bot that started that day) the first one
        after. Returns (value, ts)."""
        from .localtime import day_start_utc

        start = day_start_utc(day)
        rows = self.query(
            "SELECT total_value_xrp, ts FROM balances WHERE network=? AND bot=? AND ts<? ORDER BY ts DESC LIMIT 1",
            (network, bot, start),
        )
        if not rows:
            rows = self.query(
                "SELECT total_value_xrp, ts FROM balances WHERE network=? AND bot=? AND ts>=? ORDER BY ts ASC LIMIT 1",
                (network, bot, start),
            )
        if not rows:
            return None, None
        return Decimal(rows[0]["total_value_xrp"]), rows[0]["ts"]

    def net_transfers_since(self, network: str, bot: str, since_iso: str, until_iso: str = "9999") -> Decimal:
        """Deposits minus withdrawals (sweeps to your vault are not losses)."""
        total = Decimal(0)
        for r in self.query(
            "SELECT direction, amount_xrp FROM transfers WHERE network=? AND bot=? AND ts>=? AND ts<?",
            (network, bot, since_iso, until_iso),
        ):
            amt = Decimal(r["amount_xrp"])
            total += amt if r["direction"] == "in" else -amt
        return total

