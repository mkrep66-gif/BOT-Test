# The server (Testnet, 24/7)

DigitalOcean Droplet, Ubuntu 24.04, New York, `157.245.255.65`. Testnet only.

## Logging in

- Termux on your phone, or your computer: `ssh botadmin@157.245.255.65`
- DigitalOcean website → Droplet → **Console** (choose user `botadmin` if asked).
- Root logins and password logins are switched off; only SSH keys work.
  Keep a second key (e.g. from your computer) so losing your phone never
  locks you out.

## How it's locked down (`deploy/harden.sh`)

| What | How |
|---|---|
| Logins | SSH keys only, user `botadmin`; no root, no passwords, 3 tries per connection |
| Brute-force protection | fail2ban: 5 failed logins in 10 min → that address blocked for 1 hour |
| Firewall | Only SSH comes in. The dashboard is never exposed to the internet. |
| Updates | Security updates installed daily; if one needs a reboot it happens around 04:30 UTC (00:30 New York) and the bots start again by themselves |
| The bots | Run as user `xrplarb`, which can't log in, inside a systemd sandbox that can only write to its own data, settings, reports and keys folders. Keys file is owner-only. |

## One copy per wallet

The bots refuse to start if another copy holds the lock on the same machine,
or if a transaction from another installation appears on any bot wallet in
the last 15 minutes. While running, a bot that sees one stops itself and
alerts you. Every transaction carries a memo naming the installation that
sent it, so this works across machines.

## Buttons

**On your phone (what you use now):** Termux:Widget home-screen buttons from
`phone/termux/` (install: `curl` them into `~/.shortcuts`, see chat). Plus your
Telegram bot: `/status`, `/paper` (today's paper trading), `/kill <PIN>`, `/resume <PIN>`.

If you ever get a computer: `desktop/windows/*.bat` or `desktop/mac/*.command`:

| Button | Does |
|---|---|
| Start | Starts bots + lab noise (+ dashboard once its password is set); they also start after a reboot |
| Stop | Normal stop - trades in progress finish, then the bots exit. Not the kill switch. |
| Status | Each bot's state, today's trades and profit, problems today |
| Dashboard | Opens an encrypted SSH tunnel and the dashboard at http://localhost:8080. Close the window when done. |

On the server itself: `sudo xrplarb-ctl start|stop|restart|status`.

## Updating the code

```
cd /root/BOT-Test && git pull && sudo bash deploy/install.sh
```
(as `botadmin`: `cd ~/BOT-Test` after cloning there). Settings, token
approvals and the block list on the server are never overwritten by an update.
