"""Server health for Telegram /health: CPU now (overall and per part),
memory, disk and uptime - the same numbers as the phone's XRPL-Health button.

Read straight from /proc (Linux), sampled over one second. Read-only.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

# what each part is started as (python -m xrplarb <command>)
PARTS = (("run", "Testnet bots"), ("watch", "Paper trading"), ("dashboard", "Dashboard"), ("lab-noise", "Practice market"))
PROC = Path("/proc")


def _cpu_total() -> Tuple[int, int]:
    """(busy, total) clock ticks for the whole machine."""
    f = (PROC / "stat").read_text().splitlines()[0].split()[1:]
    v = [int(x) for x in f]
    idle = v[3] + (v[4] if len(v) > 4 else 0)
    return sum(v) - idle, sum(v)


def _parts() -> Dict[str, int]:
    """Our running parts: label -> pid."""
    found: Dict[str, int] = {}
    for d in PROC.iterdir():
        if not d.name.isdigit():
            continue
        try:
            args = (d / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        args = [a.decode(errors="replace") for a in args if a]
        if "xrplarb" not in args or "-m" not in args:
            continue
        cmd = args[args.index("xrplarb") + 1] if args.index("xrplarb") + 1 < len(args) else ""
        for c, label in PARTS:
            if cmd == c:
                found[label] = int(d.name)
    return found


def _proc_ticks(pid: int) -> Optional[int]:
    try:
        f = (PROC / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
        return int(f[11]) + int(f[12])  # utime + stime
    except (OSError, IndexError, ValueError):
        return None


def _rss_mb(pid: int) -> Optional[int]:
    try:
        for line in (PROC / str(pid) / "status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return None


async def server_health(sample_s: float = 1.0) -> str:
    if not (PROC / "stat").exists():
        return "Server health is only available on the Linux server."
    parts = _parts()
    busy0, total0 = _cpu_total()
    ticks0 = {k: _proc_ticks(p) for k, p in parts.items()}
    await asyncio.sleep(sample_s)
    busy1, total1 = _cpu_total()
    ticks1 = {k: _proc_ticks(p) for k, p in parts.items()}
    dt = max(total1 - total0, 1)
    ncpu = os.cpu_count() or 1
    lines = [f"Server health - {time.strftime('%Y-%m-%d %H:%M %Z')}",
             f"CPU in use now: {100 * (busy1 - busy0) / dt:.0f}%"]
    for _, label in PARTS:
        pid = parts.get(label)
        if pid is None:
            lines.append(f"{label}: not running")
            continue
        t0, t1 = ticks0.get(label), ticks1.get(label)
        # a process's ticks are counted per CPU; the machine total covers all CPUs
        cpu = 100 * ncpu * (t1 - t0) / dt if t0 is not None and t1 is not None else 0.0
        lines.append(f"{label}: CPU {cpu:.1f}%, memory {_rss_mb(pid) or 0} MB")
    mem = {}
    for line in (PROC / "meminfo").read_text().splitlines():
        k, v = line.split(":", 1)
        mem[k] = int(v.split()[0])
    total_mb = mem["MemTotal"] // 1024
    used_mb = (mem["MemTotal"] - mem.get("MemAvailable", mem.get("MemFree", 0))) // 1024
    lines.append(f"Memory: {used_mb} of {total_mb} MB used ({used_mb * 100 // max(total_mb, 1)}%)")
    du = shutil.disk_usage("/")
    lines.append(f"Disk: {du.used / 2**30:.1f} of {du.total / 2**30:.0f} GB used ({du.used * 100 // du.total}%)")
    up = float((PROC / "uptime").read_text().split()[0])
    lines.append(f"Up for: {int(up // 86400)}d {int(up % 86400 // 3600)}h {int(up % 3600 // 60)}m")
    lines.append("Comfortable: CPU under 50%, memory under 80%.")
    return "\n".join(lines)
