# Weekly review (testnet): 2026-10-22 to 2026-10-22

All times are your local time (America/New_York).

## The short version

- **Trades attempted:** 16
- **Win rate:** 88% (14 wins, 0 losses, 2 no-fills)
- **Profit from trades:** +1.109095 XRP (after 0.000768 XRP of network fees)
- **Change in wallet value:** +1.109095 XRP (tokens valued at what they'd really sell for; your own deposits and vault sweeps left out)

A *no-fill* is a trade the ledger cancelled as a whole because the prices had changed by the time it arrived. Nothing was bought or sold; only the tiny network fee was paid.

## Day by day

| Day | Attempts | Wins | No-fills | Win rate | Trade profit | Wallet change |
|---|---|---|---|---|---|---|
| 2026-10-22 | 16 | 14 | 2 | 88% | +1.109095 XRP | +1.109095 XRP |

## Why trades didn't fill

- 2 x price moved or someone else took the gap first (ledger cancelled the loop)

## Gaps seen but not traded (top reasons)

- 69 x gap exists only at tiny size; any real trade size loses money
- 14 x route paused after failed attempts (retry cooldown)
- 11 x another bot was using that market
- 6 x gap too small: profit after fees and safety margin below the minimum

## Anything that broke

- Nothing. No crashes, halts or warnings.

## Suggested tuning

- Nothing needs changing this week.
