# How the top bot wins: rxLfrvic9BP4atxcz433ZcdbHUknKFixM (2026-10-24)

Read-only study of the wallet's last 4,800 public transactions (ledgers
107858385–107871120, about 13.4 hours). Nothing was traded.

## What it does

- **Same building block as ours:** Batch, all-or-nothing, 2 inner payments
  (XRP -> tokens with partial payment, then tokens -> XRP with a minimum).
- **3,255 batches, 496 filled (15%), 0 losses.** Net **+109.5 XRP** in 13.4 h
  (~200 XRP/day). The 2,759 batches that cancelled cost **1.29 XRP** in fees.
- **Profit per fill:** median +0.026 XRP, top 10% +0.28 or more, best +20.0.
- **Size per trade:** median 3.8 XRP, top 10% 33+, largest 902 XRP.
- **Tickets for every batch**, so many go out at once: it fires in only ~6% of
  ledgers, but up to 9+ batches in one ledger.
- **Short life:** LastLedgerSequence is the ledger it lands in +0 to +2.
- **Fees:** 60 drops normally; ~30,000 drops on ~0.5% (busy-queue moments).
- 75 AccountDeletes paying it: short-lived helper accounts being cleaned up.

## The key finding: it backruns pending trades

Sample of 45 filled batches; for each, the ledger it landed in and the one before:

| Where the gap came from | Fills |
|---|---|
| Someone's trade **earlier in the same ledger** (it saw the pending trade and followed it in) | **36** |
| A trade in the previous ledger (reacted after close, like us) | 5 |
| No trade nearby | 3 |
| Order books only (not checked) | 1 |

So ~80% of its wins come from watching *pending* transactions and sending its
loop into the same ledger, behind the trade that opens the gap. Because the
ledger orders transactions in a fixed shuffled way (not by arrival), it sends
several batches per gap so that one lands after the trade.

## What it means for us

Reacting to closed ledgers (our live feed, ~0.6 s) only catches gaps that
survive a ledger. Most gaps never do. To compete: subscribe to pending
transactions on our pools, read the pools' post-trade ("current" ledger) state,
and fire a few small all-or-nothing batches at once - paper first, within the
existing safety rules (100 XRP per bot, 0.02 XRP minimum, block list, loss caps).
