# How it works

## 1. Reading the markets

Every ~4 seconds (about once per ledger) the supervisor reads, for every
approved token:

- the order book in both directions against XRP (top 30 offers),
- the AMM pool against XRP, if there is one,
- the books/pools between pairs of approved tokens that actually trade,
- each issuer's transfer fee,
- the current network fee and reserve.

That snapshot is shared by all bots, so 5 bots don't make 5× the requests.

## 2. Finding loops

From the markets that exist, it lists every loop
`XRP → token → … → XRP` of 3, 4 or 5 hops. Each loop is checked:

1. **Quick filter.** Multiply the best price on each hop (after pool fees and
   transfer fees). If the result is ≤ 1, even a tiny trade loses. Skip. This
   is almost every loop, almost all the time.
2. **Best size.** For loops that pass, it simulates trades from 1 XRP up to the
   bot's limit, walking the real offers and pool curves (bigger trades get
   worse prices), and picks the size with the highest profit.
3. **All costs.** Subtracts the network fee. Then applies the safety margin:
   it plans to buy 0.05% less of the final token than the simulation says, and
   re-checks profit with that.
4. **Decision.** Profit ≥ 0.02 XRP → trade. Otherwise it's logged as a missed
   gap with the exact reason (e.g. "gap too small to cover the network fee").

## 3. Executing: all-or-nothing

The loop is sent as one **Batch** transaction with the **All-Or-Nothing** flag,
containing two payments from the bot to itself:

1. *Pay up to N XRP to receive exactly X of the last token, routed through the
   middle tokens.* The ledger runs this multi-hop route as one step.
2. *Pay those X tokens to receive XRP, and accept no less than the floor.* The
   floor = XRP spent + fee + 0.02 XRP minimum profit.

If prices moved, or another bot took the gap first, either payment fails and
**the ledger cancels both**. The only cost is the network fee (~0.00004 XRP).
That's why the safety margin can be small: being wrong costs almost nothing.

### If all-or-nothing isn't available

The Batch feature has to be switched on by the ledger's validators. The bots
check on startup. If it's off:

- **Default: they don't trade.** They keep scanning and logging so you can see
  what you're missing.
- **Two-step mode (only if you agree):** the same two payments sent one after
  the other. Payment 1 is still all-or-nothing across all its hops. **The risk:**
  for about 4 seconds between payment 1 and payment 2, the bot holds the last
  token. If its price drops in that window, payment 2 fails its floor.
  - **Recovery step:** the bot immediately sells that token back to XRP at the
    best available price, accepting progressively worse prices (0.5%, 2%, 5%,
    then up to 25% below the current price) until it's sold. It records the
    loss and alerts you. If it still can't sell, the bot stops itself and
    alerts you.
  - Worst realistic case per failed trade: a few % of that one trade (≤ 100 XRP).
    The daily 10% loss cap still applies on top.
  - Two-step mode also requires a bigger safety margin (0.3%), so it trades
    less often.

To turn it on: set `allow_non_atomic: true` in `config/settings.yaml`. I won't
do that without your written OK.

## 4. Splitting routes between bots

Loops are grouped by which tokens they use. All loops over the same set of
tokens go to the same bot (they trade the same markets). Groups are then
spread evenly. Also, before trading, a bot "locks" the markets it's about to
touch. If another bot holds one, it skips and logs "market in use by botN".
So the bots never compete with each other.

## 5. Risk limits

- **Working balance:** each bot trades at most 100 XRP (20 XRP at mainnet start),
  minus the ledger reserve and a 0.5 XRP fee cushion. If profit pushes the wallet
  above its cap, the excess goes to your vault address (if set).
- **Honest valuation:** any tokens held are valued by simulating actually selling
  them into the current books and pools, including fees and price impact. A token
  nobody is buying is worth 0.
- **Daily loss cap:** today's value vs the value at your local midnight, ignoring money
  you moved in or out. Over 10% down → stop, sell tokens to XRP, alert, stay off
  until Resume.
- **Stale prices:** a trade is only sent if the prices it was worked out from
  come from a ledger that closed less than 60 seconds ago.
- **Retry cooldown:** after a failed or cancelled trade, that route rests for 30 s,
  then 60 s, 120 s … up to 30 minutes, and goes back to normal after a success.
- **Crashes:** a bot that errors is retried. 3 crashes within an hour → it's stopped
  and you're alerted. If the whole program dies, the server restarts it and it
  alerts you that it restarted.
- **Token changes:** issuers are re-checked every 5 minutes; any new fee, freeze,
  clawback or authorisation requirement → token dropped, alert sent.

## 6. Logging

All in one file, `data/xrplarb.db`:

- **trades:** every attempt (win, loss, cancelled), tx hash, XRP in/out, fee, profit
- **legs:** every swap in your wallet (sent X, received Y), plus each fee (this is the tax view)
- **opportunities:** every gap seen, executed or missed, with the reason
- **balances:** wallet value snapshots (XRP + honest token value)
- **events:** starts, stops, crashes, alerts, kill switch use

Export: `python -m xrplarb export` → `exports/xrplarb_export.xlsx` + CSVs.
The **Tax** tab follows the "universal" layout most crypto tax tools import
(Date, Sent Amount/Currency, Received Amount/Currency, Fee, TxHash).
Ask your accountant how your country treats each swap. Typically each one is a
taxable disposal.
