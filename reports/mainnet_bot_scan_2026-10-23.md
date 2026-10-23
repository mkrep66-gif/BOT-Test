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

---

# Follow-up: 6.5-hour scan (every 6th ledger, 952 ledgers, ~70,000 transactions)

Estimated for a whole day, all bots on mainnet combined:

| | Per day (estimate) |
|---|---|
| Arbitrage attempts | ~578,000 |
| Fills (2.0%) | ~11,700 |
| Net profit, everyone together | ~+163 XRP |
| Fills worth ≥ 0.02 XRP (our minimum) | ~1,280, together ~+217 XRP |
| Typical trade | risks 0.21 XRP to make 0.0008 XRP |

**So the whole market produces about 11,700 profitable trades a day.** A target of
5,000–10,000 a day for one operator would mean taking half or more of everything.

## Top 10 trading wallets (6.5 h)

| Wallet | Attempts | Fills | Won (XRP) | Submitting accounts | Wins on |
|---|---|---|---|---|---|
| `rxLfrvic9BP4atxcz433ZcdbHUknKFixM` | 7,389 | 73 (1.0%) | +5.83 | 1,225 | FUZZY, 🌊JeetTide, DROP, PLX |
| `r9ruWAaX3E4EDrMWgs7A7QbcvooMnHkaUd` | 1,313 | 30 (2.3%) | +1.95 | 291 | FUZZY, ARMY, PLX, gei |
| `raRBYswSuyUpH7h51UmWuNCw6Cj1TmpKD` | 3,866 | 10 (0.3%) | +1.24 | 195 | DROP, FUZZY, TRIPPR |
| `rBqUZzPRcVus1phirwuPd7f6QJQQsCdyax` | 1,835 | 43 (2.3%) | +0.40 | 4 | FUZZY, DeepTide, RLUSD, DROP |
| `rLMwKsJjKpyacdqFrKDjPgvtq9XJ6sA78E` | 1,374 | 29 (2.1%) | +0.25 | 403 | FUZZY, DROP, ARMY, BITx |
| `rM111p5yr8K3X9VCGBTWFwf6jNBdzaHYzm` | 454 | 16 (3.5%) | +0.25 | 43 | FUZZY, RLUSD, BITx |
| `ra111hLXnbk3SYcJKggSC6D1d2qZ6C58QR` | 452 | 10 (2.2%) | +0.12 | 42 | XAH, PLX, PHNIX |
| `rE111hpjvsc1gq1ZoRET8AAVdFEMFPG8JK` | 381 | 15 (3.9%) | +0.05 | 42 | PLX, DROP, FUZZY |
| `r4Wij5i9k9DNzW1CKXFetjCeTqWoPgsxpr` | 135 | 1 (0.7%) | +0.05 | 16 | ARMY, Xmen, gei |
| `rtaXvxk6mqixCgMETaA5jxgm3ZKB5n3FP` | 554 | 6 (1.1%) | +0.04 | 72 | DROP, gei, FUZZY |

## Where the money is

Most won: FUZZY (+5.8), DROP (+4.0), Xmen (+2.7), 🌊JeetTide (+2.2), DeepTide (+1.4).
Best routes: XRP > FUZZY > DROP > XRP (+2.58 from 2 fills), XRP > Xmen > FUZZY > XRP (+1.18).

New candidates added (unapproved): 🌊JeetTide, DeepTide, mula, ape, CODED, SLOPPY,
CRAP, nuked, scrap. Safety review: only **DeepTide** and **scrap** pass (CAUTION);
the rest are too thin (50–4,900 XRP of real liquidity).

## Auction slots

The top bots hold or are authorised on slots (Xmen, DROP, PHNIX). The slots on the
busiest pools - FUZZY (0.65% fee), TRIPPR (0.53%), PLX (0.50%) - were vacant.
Our pricing now uses the discounted fee when a slot covers all our bots; buying
one costs real XRP and is not planned before mainnet results justify it.
