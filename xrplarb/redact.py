"""Make sure secret keys can never end up in logs, alerts or the dashboard.

Every log line and every alert passes through scrub(), which removes:
  * any wallet seed we know about
  * anything shaped like an XRPL seed (starts with 's', 28-31 base58 chars)
  * any other configured secret (bot tokens, SMTP passwords)
"""

from __future__ import annotations

import logging
import os
import re
from typing import Iterable, List

SEED_RE = re.compile(r"\bs[1-9A-HJ-NP-Za-km-z]{27,31}\b")
# 'sEd' + base58 is the ed25519 seed form; covered by the pattern above.
# (We deliberately don't scrub 64-char hex: transaction hashes look like that.)

_known: List[str] = []


def register_secrets(values: Iterable[str]) -> None:
    for v in values:
        if v and len(v) >= 8 and v not in _known:
            _known.append(v)


def scrub(text: str) -> str:
    if not text:
        return text
    for v in _known:
        if v in text:
            text = text.replace(v, "[REDACTED]")
    text = SEED_RE.sub("[REDACTED-SEED]", text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        clean = scrub(msg)
        if clean != msg:
            record.msg = clean
            record.args = ()
        return True


class RedactingFormatter(logging.Formatter):
    """Scrubs the whole formatted line, including error tracebacks (which
    the filter above never sees)."""

    def format(self, record: logging.LogRecord) -> str:
        return scrub(super().format(record))


def setup_logging(level: int = logging.INFO, logfile: str | None = None) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    fmt = RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handlers: List[logging.Handler] = [logging.StreamHandler()]
    if logfile:
        os.makedirs(os.path.dirname(logfile) or ".", exist_ok=True)
        # owner-only, like the secrets file: logs hold your trading history
        os.close(os.open(logfile, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600))
        os.chmod(logfile, 0o600)
        handlers.append(logging.FileHandler(logfile))
    for h in handlers:
        h.setFormatter(fmt)
        h.addFilter(RedactingFilter())
        root.addHandler(h)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
