"""Command line. Run `python -m xrplarb --help` for the list.

Everything the system does is one of these commands; the docs explain them
in plain English."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import sys
from decimal import Decimal
from pathlib import Path

from . import secrets_store
from .config import (
    DATA_DIR,
    REPORTS_DIR,
    blocked_reason,
    load_blocklist,
    load_settings,
    load_token_entries,
    save_blocklist,
    save_token_pairs,
    save_tokens,
)
from .db import DB
from .localtime import today_local
from .redact import register_secrets, setup_logging


def _settings(args):
    return load_settings(network_override=getattr(args, "network", None))


def _net(s):
    from .xrplnet import Net

    return Net(s.rpc_urls, network=s.network, rate_per_server=s.requests_per_second_per_server)


def cmd_init(args):
    secrets_store.save(secrets_store.load())  # creates file with locked permissions
    print(f"Private settings file ready at {secrets_store.secrets_path()} (permissions: owner only).")
    if not secrets_store.get("dashboard.password_hash") or args.reset_password:
        cmd_set_password(args)


def cmd_set_password(args):
    while True:
        p1 = getpass.getpass("Choose a dashboard password (12+ characters): ")
        if len(p1) < 12:
            print("Too short.")
            continue
        if getpass.getpass("Type it again: ") != p1:
            print("Didn't match.")
            continue
        break
    secrets_store.set_value("dashboard.password_hash", secrets_store.hash_password(p1))
    secrets_store.session_key()
    print("Dashboard password saved (only a scrambled hash is stored).")


def cmd_wallets_create(args):
    from .wallets import bot_name, create_wallet

    s = _settings(args)
    net = _net(s)

    async def go():
        for i in range(1, args.count + 1):
            addr = await create_wallet(net, s.network, bot_name(i), mainnet_approved=args.i_have_written_approval)
            print(f"{bot_name(i)}: {addr}")
        if s.network == "testnet" and args.vault:
            addr = await create_wallet(net, s.network, "vault")
            print(f"vault: {addr}  (put this under vault_address.testnet in config/settings.yaml)")

    asyncio.run(go())
    print("Secret keys were saved to the private settings file only.")


def cmd_wallets_show(args):
    s = _settings(args)
    net = _net(s)

    async def go():
        for w in secrets_store.wallets(s.network):
            info = await net.account_info(w["address"])
            bal = Decimal(info["Balance"]) / 1_000_000 if info else Decimal(0)
            toks = await net.token_balances(w["address"]) if info else {}
            tstr = ", ".join(f"{v} {a.short}" for a, v in toks.items() if v != 0) or "no tokens"
            print(f"{w['name']:14} {w['address']}  {bal:>12.6f} XRP  owner objects={info.get('OwnerCount', 0) if info else '-'}  {tstr}")

    asyncio.run(go())


def cmd_discover(args):
    from .tokens import discover_tokens, probe_book_pairs

    s = _settings(args)
    net = _net(s)

    async def go():
        tokens, pairs = await discover_tokens(net, max_tokens=args.max_tokens, min_xrp_liquidity=Decimal(args.min_liquidity))
        if args.probe_books:
            extra = await probe_book_pairs(net, tokens[: args.probe_books])
            pairs = sorted(set(pairs) | set(extra), key=lambda p: (p[0].key, p[1].key))
        return tokens, pairs

    tokens, pairs = asyncio.run(go())
    existing = {(e["currency"], e["issuer"]): e for e in load_token_entries(s.network)}
    blocked = load_blocklist()
    tokens = [t for t in tokens if not blocked_reason(t, blocked)]
    for t in tokens:
        existing.setdefault((t.currency, t.issuer), {"name": t.symbol, "currency": t.currency, "issuer": t.issuer, "approved": False})
    save_tokens(s.network, list(existing.values()))
    save_token_pairs(s.network, pairs)
    print(f"Found {len(tokens)} candidate tokens and {len(pairs)} token/token markets on {s.network}.")
    print("None are approved. Next: python -m xrplarb tokens review --network", s.network)


def cmd_tokens_review(args):
    from .amounts import Asset
    from .tokens import report_markdown, review_tokens

    s = _settings(args)
    entries = load_token_entries(s.network)
    if not entries:
        sys.exit("No tokens listed yet. Run discover (or lab setup on testnet) first.")
    assets = [Asset(e["currency"], e["issuer"]) for e in entries]
    reports = asyncio.run(review_tokens(_net(s), assets, Decimal(args.min_liquidity)))
    by_key = {r.asset.key: r for r in reports}
    for e in entries:
        r = by_key[Asset(e["currency"], e["issuer"]).key]
        e["verdict"] = r.verdict
        e["review_summary"] = r.red_flags[:3] or ["no red flags"]
    save_tokens(s.network, entries)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    p = REPORTS_DIR / f"token_review_{s.network}.md"
    p.write_text(report_markdown(reports, s.network))
    print(f"Report written to {p}")
    for r in reports:
        print(f"  {r.verdict:16} {r.asset.symbol:10} {r.asset.issuer}  fee={r.transfer_fee_pct:.3f}%  liq={r.xrp_liquidity:,.0f} XRP")


def cmd_tokens_approve(args, approve=True):
    from .amounts import Asset
    from .tokens import TokenReport, assess_issuer

    s = _settings(args)
    entries = load_token_entries(s.network)
    target = Asset.from_key(args.token)
    if approve and (why := blocked_reason(target)):
        sys.exit(f"{target.key} is on your block list ({why}). Remove it with tokens-unblock first if you really mean it.")
    for e in entries:
        if e["currency"] == target.currency and e["issuer"] == target.issuer:
            if approve:
                if e.get("verdict") == "NOT RECOMMENDED" and not args.override:
                    sys.exit("This token was NOT RECOMMENDED in the review. Pass --override only if the owner insists in writing.")
                info = asyncio.run(_net(s).account_info(target.issuer))
                r = TokenReport(asset=target)
                assess_issuer(r, info)
                e["approved"] = True
                e["approved_on"] = today_local()
                e["approved_note"] = args.note or ""
                e["approval_snapshot"] = r.snapshot()
            else:
                e["approved"] = False
                e["approved_on"] = None
            save_tokens(s.network, entries)
            print(("Approved " if approve else "Revoked ") + target.key)
            return
    sys.exit("Token not found in the list.")


def cmd_tokens_block(args, block=True):
    target = args.target.strip()
    if "." not in target and not target.startswith("r"):
        sys.exit("Give CURRENCY.ISSUER (one token) or an issuer address starting with r (all its tokens).")
    key = "token" if "." in target else "issuer"
    entries = [e for e in load_blocklist() if e.get(key) != target]
    if block:
        entries.append({key: target, "reason": args.reason or "blocked by owner", "blocked_on": today_local()})
        save_blocklist(entries)
        print(f"Blocked {target}. It will never be traded; running bots drop it within seconds.")
    else:
        if len(entries) == len(load_blocklist()):
            sys.exit(f"{target} is not on the block list.")
        save_blocklist(entries)
        print(f"Removed {target} from the block list. (It still needs approval before it is traded.)")


def cmd_tokens_blocked(args):
    entries = load_blocklist()
    if not entries:
        print("Block list is empty.")
    for e in entries:
        what = e.get("token") or f"every token from {e.get('issuer')}"
        print(f"  {what}  - {e.get('reason', '')} (since {e.get('blocked_on', '?')})")


def cmd_trustlines(args):
    from .config import load_tokens
    from .wallets import ensure_trustlines, load_wallet

    s = _settings(args)
    net = _net(s)
    tokens = [t.asset for t in load_tokens(s.network)]
    if not tokens:
        sys.exit("No approved tokens.")

    async def go():
        for w in secrets_store.wallets(s.network):
            if not w["name"].startswith("bot"):
                continue
            res = await ensure_trustlines(net, load_wallet(s.network, w["name"]), tokens)
            print(w["name"], res or "all trust lines already in place")

    asyncio.run(go())


def cmd_run(args):
    from .supervisor import Supervisor

    s = _settings(args)
    setup_logging(logfile=str(DATA_DIR / f"bots-{s.network}.log"))
    asyncio.run(Supervisor(s).run())


def cmd_watch(args):
    from .watch import run_watch

    s = _settings(args)
    setup_logging(logfile=str(DATA_DIR / f"watch-{s.network}.log"))
    asyncio.run(run_watch(s, max_scans=args.max_scans))


def cmd_watch_report(args):
    from .watch import watch_report

    p = watch_report(DB(), args.network or "mainnet")
    print(p.read_text())


def cmd_dashboard(args):
    import uvicorn

    from .dashboard import create_app

    s = _settings(args)
    if not secrets_store.get("dashboard.password_hash"):
        sys.exit("Set a dashboard password first: python -m xrplarb set-password")
    register_secrets(secrets_store.all_seeds())
    uvicorn.run(create_app(DB(), s.network), host=args.host or s.dashboard_host, port=args.port or s.dashboard_port,
                proxy_headers=True, forwarded_allow_ips="127.0.0.1")


def cmd_kill(args):
    DB().set_flag("kill", "1")
    print("Kill switch ON. The running supervisor will stop all bots and convert tokens to XRP.")


def cmd_resume(args):
    db = DB()
    db.clear_flag("kill")
    db.set_flag("resume", "1")
    print("Resume requested.")


def cmd_export(args):
    from .export import export_all

    p = export_all(DB(), Path(args.out), args.network)
    print(f"Wrote {p} (+ CSV files in the same folder)")


def cmd_backup(args):
    pw = getpass.getpass("Backup passphrase (long, memorable, written down offline): ")
    if len(pw) < 14 or getpass.getpass("Again: ") != pw:
        sys.exit("Passphrase too short or didn't match.")
    secrets_store.encrypt_backup(pw, Path(args.out))
    print(f"Encrypted backup written to {args.out}. Without the passphrase it is useless - keep both safe, separately.")


def cmd_restore(args):
    pw = getpass.getpass("Backup passphrase: ")
    raw = secrets_store.decrypt_backup(pw, Path(args.file))
    import yaml

    secrets_store.save(yaml.safe_load(raw))
    print("Private settings restored.")


def cmd_secrets_show(args):
    print(secrets_store.describe())


def cmd_alerts_test(args):
    from .alerts import Alerter

    a = Alerter(_settings(args).network)
    if not a.channels:
        sys.exit("No alert channel configured yet (see docs/ALERTS.md).")
    asyncio.run(a.send("Test alert", "If you can read this, alerts work.", force=True))
    print("Sent via:", ", ".join(a.channels))


def cmd_alerts_setup(args):
    """Interactive: secrets are typed here, on the server - never pasted into chat."""
    if args.kind == "telegram":
        import httpx

        token = getpass.getpass("Telegram bot token from @BotFather (hidden): ").strip()
        input("Now send any message to your bot in Telegram, then press Enter here...")
        r = httpx.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=20).json()
        chats = {str(u["message"]["chat"]["id"]) for u in r.get("result", []) if "message" in u}
        if len(chats) != 1:
            sys.exit(f"Expected exactly one chat to have messaged the bot, found {len(chats)}. Try again.")
        pin = getpass.getpass("Choose a 4-8 digit PIN for /kill and /resume: ").strip()
        if not (pin.isdigit() and 4 <= len(pin) <= 8):
            sys.exit("PIN must be 4-8 digits.")
        secrets_store.set_value("alerts.telegram", {"bot_token": token, "chat_id": chats.pop(), "kill_pin": pin})
    elif args.kind == "email":
        secrets_store.set_value("alerts.email", {
            "smtp_host": input("SMTP server (Gmail: smtp.gmail.com): ").strip() or "smtp.gmail.com",
            "smtp_port": int(input("Port [587]: ").strip() or 587),
            "username": input("Login email: ").strip(),
            "password": getpass.getpass("App password (hidden): ").strip(),
            "to": input("Send alerts to: ").strip(),
        })
    else:
        secrets_store.set_value("alerts.deadman_url", input("healthchecks.io ping URL: ").strip())
    print("Saved to the private settings file. Test with: python -m xrplarb alerts-test")


def cmd_lab_setup(args):
    from .testnet_lab import lab_setup

    s = load_settings(network_override="testnet")
    for line in asyncio.run(lab_setup(_net(s))):
        print(line)


def cmd_lab_noise(args):
    from .testnet_lab import lab_noise

    s = load_settings(network_override="testnet")
    setup_logging()
    asyncio.run(lab_noise(_net(s), every_s=args.every, rounds=args.rounds))


def cmd_profit_history(args):
    from .history import daily_history
    from .localtime import tz_label

    s = _settings(args)
    rows = daily_history(DB(), s.network, days=args.days)
    print(f"Daily results on {s.network}, your local time ({tz_label()}):")
    print(f"  {'day':10}  {'trades':>6}  {'win rate':>8}  {'no-fills':>8}  {'trade profit':>14}  {'wallet change':>14}")
    for d in rows:
        wr = f"{d.win_rate:.0f}%" if d.win_rate is not None else "-"
        wc = f"{d.wallet_change:+.6f}" if d.wallet_change is not None else "-"
        print(f"  {d.day:10}  {d.attempts:>6}  {wr:>8}  {d.no_fills:>8}  {d.trade_profit:>+14.6f}  {wc:>14}")
    if not rows:
        print("  (no activity yet)")


def cmd_weekly_review(args):
    from .history import review_markdown, weekly_review

    s = _settings(args)
    rv = weekly_review(DB(), s.network, days=args.days)
    text = review_markdown(rv)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    p = REPORTS_DIR / f"weekly_review_{s.network}_{rv.last_day}.md"
    p.write_text(text)
    print(text)
    print(f"(saved to {p})")


def cmd_security_check(args):
    from .security import run_checks

    results = run_checks()
    for ok, msg in results:
        print(("  PASS  " if ok else "  FAIL  ") + msg)
    if all(ok for ok, _ in results):
        print("All security checks passed.")
    else:
        sys.exit("Some checks FAILED - see above. Don't run the bots until they pass.")


def cmd_check_network(args):
    s = _settings(args)
    net = _net(s)

    async def go():
        st = await net.server_state()
        batch = await net.amendment_enabled(s.batch_amendment_names)
        print(f"{s.network}: ledger {st['ledger_index']}, base fee {st['base_fee_drops']} drops, "
              f"reserve {st['reserve_base_xrp']} + {st['reserve_inc_xrp']}/object XRP")
        print("All-or-nothing Batch transactions:", f"AVAILABLE ({batch})" if batch else "NOT available")

    asyncio.run(go())


def main(argv=None):
    setup_logging(logging.WARNING)
    p = argparse.ArgumentParser(prog="xrplarb", description="XRPL arbitrage bots")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_):
        sp = sub.add_parser(name, help=help_)
        sp.set_defaults(fn=fn)
        sp.add_argument("--network", choices=["testnet", "mainnet"])
        return sp

    sp = add("init", cmd_init, "create the private settings file and set the dashboard password")
    sp.add_argument("--reset-password", action="store_true")
    add("set-password", cmd_set_password, "change the dashboard password")
    add("check-network", cmd_check_network, "show ledger status and whether atomic Batch is available")
    sp = add("wallets-create", cmd_wallets_create, "create bot wallets (testnet: funded from faucet)")
    sp.add_argument("--count", type=int, default=5)
    sp.add_argument("--vault", action="store_true", help="also create a testnet vault wallet for sweeps")
    sp.add_argument("--i-have-written-approval", action="store_true", help="required for mainnet")
    add("wallets-show", cmd_wallets_show, "show bot addresses and balances (never secrets)")
    sp = add("discover", cmd_discover, "find candidate tokens from AMM pools (read-only)")
    sp.add_argument("--max-tokens", type=int, default=40)
    sp.add_argument("--min-liquidity", default="5000")
    sp.add_argument("--probe-books", type=int, default=20, help="check token/token order books among the top N")
    sp = add("tokens-review", cmd_tokens_review, "safety-check every listed token and write a report")
    sp.add_argument("--min-liquidity", default="5000")
    sp = add("tokens-approve", cmd_tokens_approve, "approve a token (only after the owner confirms in writing)")
    sp.add_argument("token", help="CURRENCY.ISSUER")
    sp.add_argument("--note", default="")
    sp.add_argument("--override", action="store_true")
    sp = add("tokens-revoke", lambda a: cmd_tokens_approve(a, approve=False), "stop using a token")
    sp.add_argument("token")
    sp = add("tokens-block", cmd_tokens_block, "never trade a token (CURRENCY.ISSUER) or any token of an issuer (rADDRESS)")
    sp.add_argument("target")
    sp.add_argument("--reason", default="")
    sp = add("tokens-unblock", lambda a: cmd_tokens_block(a, block=False), "remove an entry from the block list")
    sp.add_argument("target")
    add("tokens-blocked", cmd_tokens_blocked, "show the block list")
    add("trustlines", cmd_trustlines, "open trust lines for approved tokens in every bot wallet")
    add("run", cmd_run, "start the trading bots")
    sp = add("watch", cmd_watch, "watch-only scanner (no keys, no trades)")
    sp.add_argument("--max-scans", type=int, default=None)
    add("watch-report", cmd_watch_report, "summarise the watch-only results")
    sp = add("dashboard", cmd_dashboard, "start the phone dashboard")
    sp.add_argument("--host")
    sp.add_argument("--port", type=int)
    sp = add("profit-history", cmd_profit_history, "day-by-day results in your local time")
    sp.add_argument("--days", type=int, default=14)
    sp = add("weekly-review", cmd_weekly_review, "plain-English review of the past week (win rate, profit, no-fills, problems)")
    sp.add_argument("--days", type=int, default=7)
    add("kill", cmd_kill, "KILL SWITCH: stop all bots, convert everything to XRP")
    add("resume", cmd_resume, "release the kill switch / loss-cap stops")
    sp = add("export", cmd_export, "export logs to Excel + CSV")
    sp.add_argument("--out", default="exports")
    sp = add("backup-secrets", cmd_backup, "write an encrypted backup of the private settings")
    sp.add_argument("--out", required=True)
    sp = add("restore-secrets", cmd_restore, "restore the private settings from an encrypted backup")
    sp.add_argument("file")
    add("secrets-show", cmd_secrets_show, "show what's stored (names/addresses only)")
    add("security-check", cmd_security_check, "check keys are stored safely and never leaked into logs/git")
    add("alerts-test", cmd_alerts_test, "send a test alert")
    sp = add("alerts-setup", cmd_alerts_setup, "set up Telegram / email / server-died watchdog alerts")
    sp.add_argument("kind", choices=["telegram", "email", "watchdog"])
    add("lab-setup", cmd_lab_setup, "testnet: create practice tokens and mispriced pools")
    sp = add("lab-noise", cmd_lab_noise, "testnet: keep knocking prices out of line")
    sp.add_argument("--every", type=float, default=60)
    sp.add_argument("--rounds", type=int, default=0)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
