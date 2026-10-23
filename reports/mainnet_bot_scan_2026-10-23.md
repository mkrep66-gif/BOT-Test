# Mainnet arbitrage bots: what the competition does (2026-10-23)

Read-only scan of public mainnet ledgers 107842309–107842908 (583 ledgers,
about 39 minutes, ~01:30–02:10 New York time). Every self-payment and every
Batch transaction was checked. Nothing was traded.

## The short version

- **One kind of arbitrage dominates:** all-or-nothing Batch loops (XRP → tokens → XRP),
  the same design as our bots.
- **11,473 attempts in 39 minutes** (about 20 per ledger, up to ~100 in a busy one),
  by **77 trading wallets**. They don't send their own transactions: 1,872 other
  accounts submit the Batches for them, so they can fire many attempts in the same
  ledger.
- **Only 2.5% fill.** The rest are cancelled by the ledger (fee only).
- **Everyone together made +1.25 XRP net** in those 39 minutes: +2.11 XRP won,
  0.86 XRP spent on fees.
- **Most wins are tiny:** median +0.0005 XRP. Only **16 fills** were worth our 0.02 XRP
  minimum (together +1.61 XRP). The single best was +0.65 XRP.
- **They demand almost no profit per attempt:** the median is +0.007% over break-even.
  They win by volume, not by size.

## The most successful trading wallets

| Wallet | Attempts | Filled | Won (XRP) |
|---|---|---|---|
| `r9ruWAaX3E4EDrMWgs7A7QbcvooMnHkaUd` | 521 | 24 (4.6%) | +0.73 |
| `rxLfrvic9BP4atxcz433ZcdbHUknKFixM` | 2,218 | 10 (0.5%) | +0.55 |
| `rLMwKsJjKpyacdqFrKDjPgvtq9XJ6sA78E` | 444 | 17 (3.8%) | +0.26 |
| `raRBYswSuyUpH7h51UmWuNCw6Cj1TmpKD` | 3,701 | 8 (0.2%) | +0.16 |
| `rErSW3gyRyCuzbvAQkQUaCYskSYBdZ1MqM` | 106 | 3 (2.8%) | +0.06 |

"Won" is before the submitting accounts' fees, which are spread over hundreds of
accounts per wallet.

## Tokens and routes they trade

Almost all small meme tokens. Attempts / fills:

FUZZY 7117/119 · TRIPPR 6691/60 · PLX 1905/47 · Xmen 1549/24 · ARMY 1427/72 ·
POLICE 1141/32 · 3RDEYE 890/12 · DROP 888/24 · gei 833/15 · RLUSD 649/29 ·
SeagullCoin 496/3 · HOOTS 493/1 · BITx 427/39 · PHNIX 378/38

Routes that paid the most: XRP > Xmen > FUZZY > XRP (+0.65, 1 of 37),
XRP > HOOTS > PLX > XRP (+0.37, 1 of 111), XRP > DROP > ARMY > XRP (+0.14, 2 of 59),
XRP > FUZZY > TRIPPR > XRP (+0.11, 14 of 444).

## What this means for us

1. **The opportunity is real but small and crowded.** In this window the whole field
   netted about 1.25 XRP. Gaps of 0.02 XRP or more appeared about once every 2–3
   minutes, and dozens of wallets race for each one.
2. **Their tokens would mostly fail our safety review** (thin pools, issuers that can
   still change the rules). They're worth *watching*, not trading blind.
3. **Copying their volume tactic (many submitters, near-zero margin) isn't
   recommended.** It's a fee-for-lottery-ticket game that only pays at scale, and
   the scale is mostly already taken.
4. **What we should do:** add these tokens as *candidates* for the mainnet
   watch-only week (each still goes through the safety review and your approval),
   and let that week measure how many gaps *we* could realistically have caught.

Caveats: one 39-minute snapshot. Busy and quiet periods differ a lot, so treat the
totals as an order of magnitude, not a forecast.
