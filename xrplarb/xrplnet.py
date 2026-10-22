"""Talking to the XRP Ledger: a small wrapper around xrpl-py's JSON-RPC client
with server failover, a concurrency limit (public servers rate-limit) and a
few convenience reads."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional

from xrpl.asyncio.clients import AsyncJsonRpcClient
from xrpl.models.requests import (
    AccountInfo,
    AccountLines,
    BookOffers,
    GenericRequest,
    LedgerEntry,
    ServerInfo,
)
from xrpl.models.requests.request import Request

from xrpl.models.currencies import XRP as XRPCurrency
from xrpl.models.currencies import IssuedCurrency

from .amounts import Asset, drops_to_xrp

log = logging.getLogger(__name__)

AMENDMENTS_INDEX = "7DB0788C020F02780A673DC74757F23823FA3014C1866E72CC4CD8B226CD6EF4"


class RPCError(RuntimeError):
    def __init__(self, error: str, result: Dict[str, Any]):
        super().__init__(error)
        self.error = error
        self.result = result


def _cur(a: Asset):
    return XRPCurrency() if a.is_xrp else IssuedCurrency(currency=a.currency, issuer=a.issuer)


def amendment_id(name: str) -> str:
    return hashlib.sha512(name.encode("ascii")).digest()[:32].hex().upper()


class Net:
    def __init__(self, urls: List[str], max_concurrency: int = 8, network: str = "testnet"):
        if not urls:
            raise ValueError("no RPC urls")
        self.urls = urls
        self.network = network
        self._idx = 0
        self._probed = False
        self.clients = [AsyncJsonRpcClient(u) for u in urls]
        self.sem = asyncio.Semaphore(max_concurrency)

    @property
    def client(self) -> AsyncJsonRpcClient:
        return self.clients[self._idx]

    async def working_client(self) -> AsyncJsonRpcClient:
        """The client for a server that answers. Use this instead of `client`
        when talking to xrpl-py helpers directly (they don't fail over)."""
        if not self._probed:
            await self.request(ServerInfo())  # rotates past unreachable servers
            self._probed = True
        return self.client

    def _rotate(self) -> None:
        self._idx = (self._idx + 1) % len(self.clients)

    async def request(self, req: Request, retries: int = 3) -> Dict[str, Any]:
        last_exc: Optional[Exception] = None
        for attempt in range(retries):
            async with self.sem:
                try:
                    resp = await self.client.request(req)
                except Exception as e:  # network trouble -> try another server
                    last_exc = e
                    log.warning("RPC %s failed on %s: %s", req.method, self.urls[self._idx], e)
                    self._rotate()
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
            result = resp.result
            if resp.is_successful():
                return result
            err = result.get("error", "unknown")
            if err in ("slowDown", "tooBusy", "noNetwork", "noCurrent", "noClosed"):
                self._rotate()
                await asyncio.sleep(1.0 * (attempt + 1))
                last_exc = RPCError(err, result)
                continue
            raise RPCError(err, result)
        raise last_exc or RuntimeError("request failed")

    # ---- reads ----------------------------------------------------------------
    async def server_state(self) -> Dict[str, Any]:
        info = (await self.request(ServerInfo()))["info"]
        vl = info.get("validated_ledger") or {}
        base_fee_xrp = Decimal(str(vl.get("base_fee_xrp", "0.00001")))
        load = Decimal(info.get("load_factor", 1))
        return {
            "ledger_index": vl.get("seq"),
            "base_fee_drops": int(base_fee_xrp * 1_000_000),
            "load_factor": load,
            "reserve_base_xrp": Decimal(str(vl.get("reserve_base_xrp", 1))),
            "reserve_inc_xrp": Decimal(str(vl.get("reserve_inc_xrp", "0.2"))),
            "network_id": info.get("network_id"),
        }

    async def account_info(self, address: str) -> Optional[Dict[str, Any]]:
        try:
            r = await self.request(AccountInfo(account=address, ledger_index="validated"))
        except RPCError as e:
            if e.error == "actNotFound":
                return None
            raise
        return r["account_data"]

    async def xrp_balance(self, address: str) -> Decimal:
        info = await self.account_info(address)
        return drops_to_xrp(info["Balance"]) if info else Decimal(0)

    async def trust_lines(self, address: str) -> List[Dict[str, Any]]:
        lines: List[Dict[str, Any]] = []
        marker = None
        while True:
            r = await self.request(
                AccountLines(account=address, ledger_index="validated", marker=marker, limit=400)
            )
            lines.extend(r.get("lines", []))
            marker = r.get("marker")
            if not marker:
                return lines

    async def token_balances(self, address: str) -> Dict[Asset, Decimal]:
        out: Dict[Asset, Decimal] = {}
        for ln in await self.trust_lines(address):
            out[Asset(ln["currency"], ln["account"])] = Decimal(ln["balance"])
        return out

    async def book_offers(self, gets: Asset, pays: Asset, limit: int = 30) -> List[Dict[str, Any]]:
        """Offers whose owners GIVE `gets` and WANT `pays` (i.e. what a taker paying
        `pays` can receive `gets` from)."""
        r = await self.request(
            BookOffers(
                taker_gets=_cur(gets),
                taker_pays=_cur(pays),
                limit=limit,
                ledger_index="validated",
            )
        )
        return r.get("offers", [])

    async def amm_info(self, a: Asset, b: Asset) -> Optional[Dict[str, Any]]:
        try:
            # xrpl-py's AMMInfo model has no ledger_index field; send it raw so we
            # read the same validated ledger as book_offers.
            r = await self.request(
                GenericRequest(method="amm_info", asset=_cur(a).to_dict(), asset2=_cur(b).to_dict(), ledger_index="validated")
            )
        except RPCError as e:
            if e.error in ("actNotFound", "entryNotFound", "invalidParams"):
                return None
            raise
        return r.get("amm")

    async def enabled_amendments(self) -> List[str]:
        try:
            r = await self.request(LedgerEntry(index=AMENDMENTS_INDEX, ledger_index="validated"))
        except RPCError:
            return []
        return [h.upper() for h in r.get("node", {}).get("Amendments", [])]

    async def amendment_enabled(self, names: List[str]) -> Optional[str]:
        enabled = set(await self.enabled_amendments())
        for n in names:
            if amendment_id(n) in enabled:
                return n
        return None
