"""Making sure the same wallets are never traded from two places at once.

Two copies of the bots using the same wallets would fight over the same
sequence numbers and balances, and each would think the other's trades were
its own losses. Two guards:

  * on one machine: a lock file - a second `run` on the same machine refuses
    to start
  * across machines: every transaction we send carries a memo naming this
    installation. Before starting, and every couple of minutes while running,
    each bot reads its wallet's recent history on the ledger. A transaction
    signed by another installation means the wallet is in use elsewhere:
    the bots refuse to start, or a running bot stops itself and alerts you.
"""

from __future__ import annotations

import fcntl
import os
import secrets as pysecrets
import socket
import time
from typing import Any, Dict, List, Optional

from xrpl.models.transactions import Memo

from .config import DATA_DIR

MEMO_TYPE = "xrplarb/instance"
RIPPLE_EPOCH = 946684800
INNER_BATCH_FLAG = 0x40000000
# Activity from another installation within this window blocks a start.
LOOKBACK_S = 15 * 60

_cached: Optional[str] = None


def _hex(s: str) -> str:
    return s.encode().hex().upper()


def instance_id() -> str:
    """A stable name for this installation (host name + random tag), kept in
    data/instance_id. Contains nothing secret."""
    global _cached
    if _cached:
        return _cached
    p = DATA_DIR / "instance_id"
    if p.exists():
        _cached = p.read_text().strip()
    else:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _cached = f"{socket.gethostname()[:24]}-{pysecrets.token_hex(3)}"
        p.write_text(_cached + "\n")
    return _cached


def memo() -> Memo:
    return Memo(memo_type=_hex(MEMO_TYPE), memo_data=_hex(instance_id()))


def tx_instance(tx: Dict[str, Any]) -> Optional[str]:
    """Which installation sent this transaction (None if it carries no tag)."""
    for m in tx.get("Memos") or []:
        mm = m.get("Memo", {})
        if str(mm.get("MemoType", "")).upper() == _hex(MEMO_TYPE):
            try:
                return bytes.fromhex(mm.get("MemoData", "")).decode()
            except ValueError:
                return "?"
    return None


def foreign_transactions(
    address: str, entries: List[Dict[str, Any]], since_unix: float, me: Optional[str] = None
) -> List[Dict[str, str]]:
    """Transactions signed by `address` after `since_unix` that this
    installation didn't send. account_tx entries in, [{hash, by}] out."""
    me = me or instance_id()
    out = []
    for e in entries:
        tx = e.get("tx_json") or e.get("tx") or {}
        meta = e.get("meta") or {}
        if tx.get("Account") != address:
            continue  # incoming payments (faucet, vault...) are fine
        if meta.get("ParentBatchID") or int(tx.get("Flags", 0) or 0) & INNER_BATCH_FLAG:
            continue  # the inner parts of our own Batch carry no memo
        when = int(tx.get("date", 0) or 0) + RIPPLE_EPOCH
        if when < since_unix:
            continue
        by = tx_instance(tx)
        if by != me:
            out.append({"hash": e.get("hash") or tx.get("hash", ""), "by": by or "unknown (no xrplarb tag)"})
    return out


async def check_wallet(net, address: str, since_unix: float) -> List[Dict[str, str]]:
    entries = await net.account_tx(address, limit=40)
    return foreign_transactions(address, entries, since_unix)


class RunLock:
    """Only one `run` per network on this machine."""

    def __init__(self, network: str):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.path = DATA_DIR / f"run-{network}.lock"
        self.fd: Optional[int] = None

    def acquire(self) -> None:
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise SystemExit(
                "The bots are already running on this machine (another `run` holds the lock). "
                "Stop that one first."
            )
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()} {time.strftime('%Y-%m-%d %H:%M:%S')}\n".encode())
        self.fd = fd

    def release(self) -> None:
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None
