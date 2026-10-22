# XRPL Arbitrage Bots

A system of up to 5 bots that look for price gaps on the XRP Ledger's built-in
exchange and trade them only when the profit is confirmed after every cost.

**Current status: built and tested offline. Not yet connected to Testnet (see
"What's blocking" below). No real XRP is used anywhere.**

---

## What it does, in plain English

Sometimes prices on the XRP Ledger get slightly out of line. For example:

> 10 XRP buys 5 USD → 5 USD buys 4.6 EUR → 4.6 EUR buys 10.08 XRP

You end up with more XRP than you started with. That round trip is a
**loop**. The bots look for loops of 3 to 5 steps that start and end in XRP,
across both kinds of market on the ledger:

- **order books** (people's standing buy/sell offers), and
- **AMM pools** (automatic market-maker pools that always quote a price).

For every loop it finds, the bot works out the best trade size and subtracts
**every** cost before deciding:

| Cost | How it's handled |
|---|---|
| Network fee | Read live from the ledger, plus a 20% margin. Trade skipped if fees spike. |
| AMM pool fees | Built into the pool price math (each pool's own fee). |
| Token issuer transfer fees | Charged on every hop that moves that token. |
| Slippage (price moves as you trade) | Simulated by walking the actual offers/pool, plus a safety margin. |
| Reserve costs | Each token held needs 0.2 XRP locked (refundable). That XRP is kept aside and never traded. |

Only if what's left is at least **0.02 XRP profit** does it trade.

### All-or-nothing trades

Each loop is sent as **one "Batch" transaction marked all-or-nothing**. Either
the whole loop completes at our prices or the ledger cancels all of it and we
only pay the tiny network fee (about 0.00004 XRP). We never end up halfway
round holding a token we didn't want.

The Batch feature is a fairly new ledger feature. The bots check automatically
whether it's switched on (`python -m xrplarb check-network`). **If it isn't, the
bots will watch and log but not trade** until you've read the risk explanation
in [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md#if-all-or-nothing-isnt-available)
and agreed to the two-step fallback.

## Your rules, and where each is enforced

| Your rule | How |
|---|---|
| Testnet only until you say so in writing | `allow_mainnet_trading: false` in settings; the bots refuse to start on mainnet. Mainnet wallets can't be created without a special flag. |
| Max 100 XRP per bot | Hard limit in the code. Settings above 100 are rejected. Extra XRP is moved to your own "vault" address if you set one. |
| Only approved tokens | Bots only trade tokens marked `approved: true`, which I only set after you confirm. Tokens the review marks NOT RECOMMENDED need an extra override. |
| Re-check tokens | Every 5 minutes each issuer is re-checked; if a fee goes up or freeze/clawback appears, that token is dropped instantly and you get an alert. |
| 10%/day loss cap | Wallet valued every 30 s with tokens priced at **what they'd actually sell for right now**. Over 10% down → bot stops, sells tokens back to XRP, alerts you, stays off until you press Resume. |
| Kill switch from phone | Big red button on the dashboard (type STOP), or Telegram `/kill <PIN>`. Stops every bot and converts everything to XRP. |
| Log everything | Every trade, fee, win, loss and every gap seen-but-missed **with the reason**. |
| Spreadsheet for taxes | Dashboard → "Download Excel", or `python -m xrplarb export`. Includes a tax tab in the layout most crypto tax tools import. |
| Secrets never shown | Keys live only in `secrets/secrets.yaml` (owner-only permissions). Every log line, alert and dashboard page is filtered for anything key-shaped. |

## Honest expectations

Please read this before getting excited:

- **XRPL arbitrage is competitive.** Professional bots, some running their own
  ledger servers, look for exactly these gaps. The ledger's own payment engine
  also routes payments through the best prices automatically, which closes many
  gaps before anyone can trade them.
- **Gaps are usually tiny.** With 100 XRP per bot, a typical winning loop might
  make a few hundredths of an XRP. Many days may have zero trades.
- **Testnet proves the machinery, not the money.** Testnet has almost no real
  trading, so I built a "lab" that creates practice tokens and deliberately
  mispriced pools. That shows the bots work; it says nothing about real profit.
- **The mainnet watch-only week is the real test.** It prices every loop on the
  real network exactly as a bot would, without trading. If that report shows
  few or no catchable gaps, the honest conclusion is "don't go live", and I'll
  tell you so.

## About running 5 bots

You asked for 5. The system supports 1–5. My honest view: **fewer is likely
better.**

- One bot can scan every route; splitting routes across 5 wallets doesn't find
  more gaps. It only helps when several separate gaps are open *at the same
  moment*.
- Each wallet locks up its own reserve: 1 XRP base + 0.2 XRP per token. With
  10 tokens that's 3 XRP locked per bot (15 XRP for 5 bots vs 3 XRP for 1).
- 5 bots × 100 XRP = 500 XRP at risk instead of 100.

The watch-only report includes a "Would more bots help?" section that measures
how often 2+ gaps were open at once. I suggest we let that decide. For Testnet
I've kept 5 (free) so you can see them working side by side.

## What's blocking right now

This build environment's network blocks the XRP Ledger servers
(`s.altnet.rippletest.net`, `faucet.altnet.rippletest.net`, `xrplcluster.com`,
`s1.ripple.com`, `s2.ripple.com`), so I couldn't create Testnet wallets or scan
yet. Either allow those hosts in this environment's network settings, or run it
on a small server or your own computer. Steps are in [docs/RUNBOOK.md](docs/RUNBOOK.md).

## Documents

- [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md): the strategy, costs, atomic vs two-step, recovery
- [docs/RUNBOOK.md](docs/RUNBOOK.md): every step from Testnet to the mainnet watch, in order
- [docs/SECRETS_AND_BACKUP.md](docs/SECRETS_AND_BACKUP.md): where keys live and how to back them up
- [docs/ALERTS.md](docs/ALERTS.md): Telegram / email / "server died" alerts, and the kill switch
- [docs/GOING_LIVE.md](docs/GOING_LIVE.md): renting a server (costs first), mainnet with 1 bot and 20 XRP

## For a technical helper

Python 3.11+, `pip install -r requirements.txt`, `python -m pytest`.
Package layout: `xrplarb/quote.py` (pool/book math), `routes.py`, `scanner.py`,
`executor.py` (Batch building/submission), `bot.py`, `supervisor.py`, `risk.py`,
`watch.py`, `dashboard.py`, `export.py`, `tokens.py`, `secrets_store.py`.
