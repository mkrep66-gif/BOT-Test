"""`security-check`: confirms wallet keys are stored safely and have never
leaked into logs, the logbook, reports, exports, settings or git.

It prints PASS/FAIL lines only - never a key, not even part of one.
"""

from __future__ import annotations

import os
import sqlite3
import stat
import subprocess
from pathlib import Path
from typing import Iterable, List, Tuple

from . import secrets_store
from .config import CONFIG_DIR, DATA_DIR, REPORTS_DIR, ROOT
from .redact import SEED_RE

TEXT_SUFFIXES = {".log", ".md", ".yaml", ".yml", ".csv", ".txt", ".json", ".html"}


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def _contains_secret(text: str, seeds: List[str]) -> bool:
    return any(s in text for s in seeds) or SEED_RE.search(text) is not None


def _text_files(dirs: Iterable[Path]) -> Iterable[Path]:
    for d in dirs:
        if not d.exists():
            continue
        for p in d.rglob("*"):
            if p.is_file() and p.suffix.lower() in TEXT_SUFFIXES:
                yield p


def _db_files(dirs: Iterable[Path]) -> Iterable[Path]:
    for d in dirs:
        if d.exists():
            yield from (p for p in d.rglob("*.db") if p.is_file())


def _scan_db(path: Path, seeds: List[str]) -> List[str]:
    hits = []
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for t in tables:
            for row in conn.execute(f"SELECT * FROM {t}"):
                if any(isinstance(v, str) and _contains_secret(v, seeds) for v in row):
                    hits.append(f"{path.name}:{t}")
                    break
    finally:
        conn.close()
    return hits


def _git(*args: str) -> Tuple[int, str]:
    try:
        r = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=120)
        return r.returncode, r.stdout
    except (OSError, subprocess.TimeoutExpired):
        return -1, ""


def run_checks() -> List[Tuple[bool, str]]:
    out: List[Tuple[bool, str]] = []
    path = secrets_store.secrets_path()
    if not path.exists():
        return [(True, "No private settings file yet (nothing to protect).")]

    if os.name == "posix":
        fm, dm = _mode(path), _mode(path.parent)
        out.append((fm & 0o077 == 0, f"Keys file is owner-only (permissions {fm:o}; want 600)"))
        out.append((dm & 0o077 == 0, f"Keys folder is owner-only (permissions {dm:o}; want 700)"))
        out.append((path.stat().st_uid == os.getuid(), "Keys file belongs to the user running the bots"))
    try:
        seeds = secrets_store.all_seeds()
        out.append((True, f"Keys file readable by its owner ({len(seeds)} wallet keys)"))
    except secrets_store.SecretsError as e:
        return out + [(False, str(e))]

    code, _ = _git("check-ignore", "-q", str(path))
    if code in (0, 1):
        out.append((code == 0, "Keys file is excluded from git (.gitignore)"))
        _, tracked = _git("ls-files", "secrets")
        bad = [t for t in tracked.split() if not t.endswith("README.md")]
        out.append((not bad, "No key files committed to git" + (f" (found: {', '.join(bad)})" if bad else "")))
        _, history = _git("log", "--all", "-p", "--no-color")
        out.append((not any(s in history for s in seeds), "No wallet key appears anywhere in git history"))

    log_dirs = [DATA_DIR, REPORTS_DIR, CONFIG_DIR, ROOT / "exports"]
    leaks = [str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)
             for p in _text_files(log_dirs) if _contains_secret(p.read_text(errors="ignore"), seeds)]
    out.append((not leaks, "No key in logs, reports, exports or settings" + (f" - FOUND IN: {', '.join(leaks)}" if leaks else "")))

    db_hits = [h for p in _db_files([DATA_DIR]) for h in _scan_db(p, seeds)]
    out.append((not db_hits, "No key in the trade logbook" + (f" - FOUND IN: {', '.join(db_hits)}" if db_hits else "")))

    for p in DATA_DIR.glob("*.log") if DATA_DIR.exists() else []:
        if os.name == "posix" and _mode(p) & 0o077:
            out.append((False, f"Log {p.name} is readable by other users (chmod 600 {p})"))
    return out
