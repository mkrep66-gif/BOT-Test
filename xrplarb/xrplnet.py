"""Talking to the XRP Ledger: a small wrapper around xrpl-py's JSON-RPC client.

Being a good citizen on public servers:
  * one persistent (keep-alive) HTTP connection pool, not a new connection
    per request
  * a steady pace per server (requests are spaced out, never sent in bursts)
  * price reads are spread across every configured server
  * a server that says it's overloaded (slowDown/tooBusy, HTTP 429/503) or
    stops answering is left alone for a while, longer each time it happens
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from decimal import Decimal
from json import JSONDecodeError
from typing import Any, Dict, List, Optional

import httpx
from xrpl.asyncio.clients import AsyncJsonRpcClient
from xrpl.asyncio.clients.client import REQUEST_TIMEOUT
from xrpl.asyncio.clients.exceptions import XRPLRequestFailureException
from xrpl.asyncio.clients.utils import json_to_response, request_to_json_rpc
from xrpl.models.requests import (
    AccountInfo,
    AccountLines,
    AccountOffers,
    BookOffers,
    GenericRequest,
    LedgerEntry,
    ServerInfo,
)
from xrpl.models.requests.request import Request
from xrpl.models.response import Response

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


BUSY_ERRORS = ("slowDown", "tooBusy", "noNetwork", "noCurrent", "noClosed")
MAX_COOLDOWN_S = 120.0


class ServerBusy(RuntimeError):
    """The server answered HTTP 429/503: it wants us to slow down."""


class PersistentJsonRpcClient(AsyncJsonRpcClient):
    """xrpl-py's client opens a new HTTP connection for every request. This one
    keeps a keep-alive pool per server, so repeated price checks are faster and
    lighter on both ends."""

    def __init__(self, url: str, pool: Optional["_HttpPool"] = None):
        super().__init__(url)
        self._pool = pool or _HttpPool()

    async def reset(self) -> None:
        """Drop this server's connections. httpx doesn't always free a pool
        slot when a connection attempt fails (e.g. refused by a proxy); without
        this, enough failures leave no free slots at all."""
        await self._pool.close()

    async def _request_impl(self, request: Request, *, timeout: float = REQUEST_TIMEOUT) -> Response:
        http = self._pool.get()
        response = await http.post(self.url, json=request_to_json_rpc(request), timeout=timeout)
        if response.status_code in (429, 503):
            raise ServerBusy(f"HTTP {response.status_code}")
        try:
            return json_to_response(response.json())
        except JSONDecodeError:
            raise XRPLRequestFailureException({"error": response.status_code, "error_message": response.text})


class _HttpPool:
    """One httpx client per event loop (a client can't be shared across
    asyncio.run() calls)."""

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def get(self) -> httpx.AsyncClient:
        loop = asyncio.get_running_loop()
        if self._client is None or self._loop is not loop or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=REQUEST_TIMEOUT,
                limits=httpx.Limits(max_connections=16, max_keepalive_connections=8, keepalive_expiry=60),
            )
            self._loop = loop
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            try:
                await self._client.aclose()
            except Exception:
                pass
        self._client = None


class _Server:
    """Pace and health of one server."""

    def __init__(self, url: str, client: AsyncJsonRpcClient, rate_per_s: float):
        self.url = url
        self.client = client
        self.interval = 1.0 / rate_per_s if rate_per_s > 0 else 0.0
        self.next_slot = 0.0
        self.cooldown_until = 0.0
        self.strikes = 0
        self.requests = 0
        self.failures = 0
        self.backoffs = 0

    def available(self, now: float) -> bool:
        return now >= self.cooldown_until

    async def wait_turn(self) -> None:
        """Reserve the next free slot on this server's steady schedule."""
        now = time.monotonic()
        slot = max(now, self.next_slot)
        self.next_slot = slot + self.interval
        if slot > now:
            await asyncio.sleep(slot - now)

    def back_off(self, why: str) -> float:
        now = time.monotonic()
        if now < self.cooldown_until:
            # several requests failing together count as one strike
            return self.cooldown_until - now
        self.strikes += 1
        self.backoffs += 1
        wait = min(MAX_COOLDOWN_S, 2.0 ** self.strikes)
        self.cooldown_until = now + wait
        log.warning("server %s: %s - leaving it alone for %.0fs", self.url, why, wait)
        return wait

    def ok(self) -> None:
        self.strikes = 0


class Net:
    def __init__(
        self,
        urls: List[str],
        max_concurrency: int = 8,
        network: str = "testnet",
        rate_per_server: float = 8.0,
    ):
        if not urls:
            raise ValueError("no RPC urls")
        self.urls = urls
        self.network = network
        self.servers = [_Server(u, PersistentJsonRpcClient(u), rate_per_server) for u in urls]
        self._next = 0
        self._writer: Optional[int] = None
        self.sem = asyncio.Semaphore(max_concurrency)

    @property
    def clients(self) -> List[AsyncJsonRpcClient]:
        return [sv.client for sv in self.servers]

    @clients.setter
    def clients(self, value: List[AsyncJsonRpcClient]) -> None:  # used by tests
        for sv, c in zip(self.servers, value):
            sv.client = c

    @property
    def client(self) -> AsyncJsonRpcClient:
        return self.servers[self._writer or 0].client

    async def working_client(self) -> AsyncJsonRpcClient:
        """One fixed server that answers, for signing/submitting and following a
        transaction (xrpl-py helpers don't fail over, and a transaction should be
        tracked on the server it was sent to)."""
        if self._writer is not None and self.servers[self._writer].available(time.monotonic()):
            return self.servers[self._writer].client
        last: Optional[Exception] = None
        for i in self._order():
            try:
                await self._send(self.servers[i], ServerInfo())
            except Exception as e:
                last = e
                continue
            self._writer = i
            return self.servers[i].client
        raise last or RuntimeError("no server answered")

    async def report_failure(self, client: AsyncJsonRpcClient, error: Exception) -> None:
        """Called when a direct xrpl-py call on `client` hit network trouble:
        rest that server and pick another one for the next transaction."""
        for i, sv in enumerate(self.servers):
            if sv.client is client:
                sv.failures += 1
                sv.back_off(f"transaction call failed: {type(error).__name__}")
                if isinstance(sv.client, PersistentJsonRpcClient):
                    await sv.client.reset()
                if self._writer == i:
                    self._writer = None
                return

    def _order(self) -> List[int]:
        """Servers to try next: round-robin over those not cooling down."""
        now = time.monotonic()
        n = len(self.servers)
        idx = [(self._next + k) % n for k in range(n)]
        self._next = (self._next + 1) % n
        ready = [i for i in idx if self.servers[i].available(now)]
        return ready or sorted(idx, key=lambda i: self.servers[i].cooldown_until)

    async def _send(self, sv: _Server, req: Request) -> Dict[str, Any]:
        now = time.monotonic()
        if not sv.available(now):
            await asyncio.sleep(sv.cooldown_until - now)
        async with self.sem:
            await sv.wait_turn()
            sv.requests += 1
            try:
                resp = await sv.client.request(req)
            except ServerBusy as e:
                sv.failures += 1
                sv.back_off(f"overloaded ({e})")
                raise
            except Exception as e:  # network trouble
                sv.failures += 1
                sv.back_off(f"{req.method.value} failed: {type(e).__name__} {e}".strip())
                if isinstance(e, httpx.TransportError) and isinstance(sv.client, PersistentJsonRpcClient):
                    await sv.client.reset()
                raise
        result = resp.result
        if resp.is_successful():
            sv.ok()
            return result
        err = result.get("error", "unknown")
        if err in BUSY_ERRORS:
            sv.failures += 1
            sv.back_off(f"says {err}")
        raise RPCError(err, result)

    async def request(self, req: Request, retries: int = 3) -> Dict[str, Any]:
        last_exc: Optional[Exception] = None
        for attempt in range(retries):
            sv = self.servers[self._order()[0]]
            try:
                return await self._send(sv, req)
            except RPCError as e:
                if e.error not in BUSY_ERRORS:
                    raise
                last_exc = e
            except Exception as e:
                last_exc = e
        raise last_exc or RuntimeError("request failed")

    def health(self, reset: bool = False) -> List[Dict[str, Any]]:
        """Counters per server since the last reset."""
        now = time.monotonic()
        out = [
            {
                "url": sv.url,
                "requests": sv.requests,
                "failures": sv.failures,
                "backoffs": sv.backoffs,
                "cooling_down_s": max(0.0, round(sv.cooldown_until - now, 1)),
            }
            for sv in self.servers
        ]
        if reset:
            for sv in self.servers:
                sv.requests = sv.failures = sv.backoffs = 0
        return out

    async def close(self) -> None:
        for sv in self.servers:
            if isinstance(sv.client, PersistentJsonRpcClient):
                await sv.client.reset()

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
            # seconds since that validated ledger closed, as seen by the server
            "ledger_age_s": float(vl.get("age", 0) or 0),
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

    async def account_offers(self, address: str) -> List[Dict[str, Any]]:
        """Standing orders (offers) owned by this account."""
        offers: List[Dict[str, Any]] = []
        marker = None
        while True:
            r = await self.request(AccountOffers(account=address, ledger_index="validated", marker=marker, limit=200))
            offers.extend(r.get("offers", []))
            marker = r.get("marker")
            if not marker:
                return offers

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
