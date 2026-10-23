"""Know the moment a ledger closes, so the bots scan straight away instead
of on a fixed timer (which can leave a fresh gap unseen for seconds).

Keeps a WebSocket subscription to the "ledger" stream on the first server
that answers, reconnecting (and moving to the next server) if it drops. If
no server answers, `wait_next` simply times out and the caller falls back
to its timer - so this can only make scanning faster, never stop it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import List, Optional

from xrpl.asyncio.clients import AsyncWebsocketClient
from xrpl.models.requests import StreamParameter, Subscribe

log = logging.getLogger(__name__)


class LedgerStream:
    def __init__(self, urls: List[str]):
        self.urls = urls
        self.latest: Optional[int] = None
        self.closed_at: float = 0.0
        self.connected = False
        self.reconnects = 0
        self._event = asyncio.Event()
        self._stop = False

    async def run(self) -> None:
        if not self.urls:
            return
        i, backoff = 0, 1.0
        while not self._stop:
            url = self.urls[i % len(self.urls)]
            try:
                async with AsyncWebsocketClient(url) as client:
                    await client.send(Subscribe(streams=[StreamParameter.LEDGER]))
                    self.connected, backoff = True, 1.0
                    log.info("ledger stream connected: %s", url)
                    async for msg in client:
                        if msg.get("type") == "ledgerClosed":
                            self.latest = int(msg["ledger_index"])
                            self.closed_at = time.time()
                            self._event.set()
                        if self._stop:
                            return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("ledger stream %s dropped: %s", url, type(e).__name__)
            self.connected = False
            self.reconnects += 1
            i += 1
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    async def wait_next(self, timeout: float) -> bool:
        """Wait for the next ledger close (True) or the timeout (False)."""
        self._event.clear()
        try:
            await asyncio.wait_for(self._event.wait(), timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def stop(self) -> None:
        self._stop = True
