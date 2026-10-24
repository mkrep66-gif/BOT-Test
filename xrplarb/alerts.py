"""Alerts to your phone.

Supported channels (set up any or all - see docs/ALERTS.md):
  * Telegram  - free, instant, and lets you send /kill and /status back
  * Email     - via any SMTP account (e.g. a Gmail "app password")
Every message is scrubbed for secrets before it leaves the machine.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
import time
from email.message import EmailMessage
from typing import Awaitable, Callable, Dict, Optional

import httpx

from . import secrets_store
from .redact import scrub

log = logging.getLogger(__name__)


class Alerter:
    def __init__(self, network: str, db=None, min_interval_s: float = 60.0):
        self.network = network
        self.db = db
        self.min_interval_s = min_interval_s
        self._last: Dict[str, float] = {}
        tg = secrets_store.get("alerts.telegram", {}) or {}
        em = secrets_store.get("alerts.email", {}) or {}
        self.tg_token: Optional[str] = tg.get("bot_token")
        self.tg_chat: Optional[str] = str(tg.get("chat_id")) if tg.get("chat_id") else None
        self.kill_pin: Optional[str] = str(tg.get("kill_pin")) if tg.get("kill_pin") else None
        self.email = em if em.get("smtp_host") else None
        self._tg_offset = 0

    @property
    def channels(self) -> list:
        ch = []
        if self.tg_token and self.tg_chat:
            ch.append("telegram")
        if self.email:
            ch.append("email")
        return ch

    async def send(self, subject: str, body: str = "", *, key: Optional[str] = None, force: bool = False) -> None:
        """Send an alert. `key` de-duplicates repeats within min_interval_s."""
        k = key or subject
        now = time.time()
        if not force and now - self._last.get(k, 0) < self.min_interval_s:
            return
        self._last[k] = now
        subject = scrub(f"[XRPL bots/{self.network}] {subject}")
        body = scrub(body)
        log.warning("ALERT: %s %s", subject, body)
        if self.db is not None:
            self.db.event(self.network, "system", "ALERT", "alert", f"{subject} {body}")
        tasks = []
        if self.tg_token and self.tg_chat:
            tasks.append(self._telegram(f"{subject}\n{body}".strip()))
        if self.email:
            tasks.append(asyncio.to_thread(self._email, subject, body or subject))
        for r in await asyncio.gather(*tasks, return_exceptions=True):
            if isinstance(r, Exception):
                log.error("alert delivery failed: %s", scrub(str(r)))

    async def _telegram(self, text: str) -> None:
        url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(url, json={"chat_id": self.tg_chat, "text": text[:4000]})
            r.raise_for_status()

    def _email(self, subject: str, body: str) -> None:
        em = self.email or {}
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = em.get("from") or em.get("username")
        msg["To"] = em["to"]
        msg.set_content(body)
        port = int(em.get("smtp_port", 587))
        ctx = ssl.create_default_context()
        if port == 465:
            with smtplib.SMTP_SSL(em["smtp_host"], port, context=ctx, timeout=20) as s:
                s.login(em["username"], em["password"])
                s.send_message(msg)
        else:
            with smtplib.SMTP(em["smtp_host"], port, timeout=20) as s:
                s.starttls(context=ctx)
                s.login(em["username"], em["password"])
                s.send_message(msg)

    async def poll_commands(self, handler: Callable[[str], Awaitable[str]], menu=()) -> None:
        """Listen for Telegram commands from YOUR chat only (/status, /kill <pin>, /resume <pin>).
        `menu`: (command, description) pairs shown in Telegram's "/" menu."""
        if not (self.tg_token and self.tg_chat):
            return
        url = f"https://api.telegram.org/bot{self.tg_token}/getUpdates"
        async with httpx.AsyncClient(timeout=40) as c:
            if menu:
                try:  # best effort: the menu is a convenience
                    await c.post(f"https://api.telegram.org/bot{self.tg_token}/setMyCommands",
                                 json={"commands": [{"command": k, "description": d} for k, d in menu]})
                except Exception as e:
                    log.warning("telegram menu not set: %s", scrub(str(e)))
            while True:
                try:
                    r = await c.get(url, params={"timeout": 30, "offset": self._tg_offset})
                    for upd in r.json().get("result", []):
                        self._tg_offset = upd["update_id"] + 1
                        m = upd.get("message") or {}
                        if str((m.get("chat") or {}).get("id")) != self.tg_chat:
                            continue  # ignore anyone else
                        reply = await handler((m.get("text") or "").strip())
                        if reply:
                            await self._telegram(scrub(reply))
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.warning("telegram poll error: %s", scrub(str(e)))
                    await asyncio.sleep(10)
