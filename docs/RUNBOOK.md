# Runbook: the order we do things in

Each stage needs your OK before the next. I'll run the commands; this is so
you can follow along. ✋ marks a point where I need something from you.

## Stage 0: Network access (blocked right now)

The environment I'm building in can't reach the XRP Ledger. Two options:

- **A. Allow it here (free, quickest for Testnet):** in this cloud
  environment's settings → Network access, add these hosts:
  `s.altnet.rippletest.net`, `faucet.altnet.rippletest.net`, `xrplcluster.com`,
  `s1.ripple.com`, `s2.ripple.com` (and `api.telegram.org` for Telegram alerts).
  Caveat: this environment shuts down when idle, so it's fine for Testnet tests
  but **not** for the week-long mainnet watch.
- **B. Rent the small server now** (~$5–7/month, details and costs in
  GOING_LIVE.md) and do everything there. ✋ Needs your OK to spend money.

## Stage 1: Testnet (free, fake XRP)

```
python -m xrplarb init                         # private settings + dashboard password ✋ (you choose the password)
python -m xrplarb check-network --network testnet   # is all-or-nothing Batch available?
python -m xrplarb wallets-create --network testnet --count 5 --vault
python -m xrplarb lab-setup                    # practice tokens TUS, TEU, TGB + mispriced pools
python -m xrplarb tokens-review --network testnet   # safety report -> reports/token_review_testnet.md
```
✋ You read the token report and confirm which tokens to approve (in writing in chat).
```
python -m xrplarb tokens-approve TUS.<issuer> --network testnet
python -m xrplarb trustlines --network testnet
python -m xrplarb run --network testnet        # bots
python -m xrplarb lab-noise                    # keeps creating gaps to catch
python -m xrplarb dashboard                    # phone dashboard
```
We test: trades executing, cancelled trades costing only the fee, missed-gap
logging, the loss cap (by forcing a tiny limit), the kill switch from your
phone, alerts, and the spreadsheet export.

## Stage 2: Mainnet watch-only (no keys, no money, ≥ 7 days)

```
python -m xrplarb discover --network mainnet       # finds liquid tokens from AMM pools
python -m xrplarb tokens-review --network mainnet  # safety report for real tokens
python -m xrplarb watch --network mainnet          # runs for a week
python -m xrplarb watch-report                     # the verdict
```
This needs a machine that stays on for a week (option B above, or your own computer).

The report shows how many profitable-after-costs gaps appeared, how long they
lasted, how many we could realistically have caught, the best-case profit,
and whether more than one bot would ever have helped. **If the numbers are
poor, I'll recommend not going live.**

## Stage 3: Mainnet, 1 bot, 20 XRP (only with written approval)

See GOING_LIVE.md.
