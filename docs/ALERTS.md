# Alerts and the kill switch

You get an alert when: a bot starts or stops, crashes, is stopped after
repeated crashes, hits its daily loss cap, can't sell a leftover token,
a token's issuer changes settings, market data keeps failing, the program
restarts unexpectedly, or the kill switch is used.

## Options. Pick one or more.

| Option | Cost | Setup | Notes |
|---|---|---|---|
| **Telegram** (recommended) | Free | ~5 min | Instant push to your phone, and you can reply `/status`, `/kill <PIN>`, `/resume <PIN>` |
| **Email** | Free | ~5 min | Works with Gmail using an "app password". Slower, and easier to miss |
| **"Server died" watchdog** (healthchecks.io) | Free tier | ~3 min | Covers the one case the bots can't report themselves: the whole server going down. If the bots stop checking in for 5 minutes, *it* emails/Telegrams you |

I recommend **Telegram + the watchdog**.

### Telegram setup
1. In Telegram, message **@BotFather**, send `/newbot`, follow the prompts. It gives you a *token*.
2. Send any message to your new bot.
3. On the server run `python -m xrplarb alerts-setup telegram`. It asks for the
   token (typed hidden), finds your chat automatically, and asks you to pick a
   4–8 digit kill PIN. Please don't paste the token in chat.

### Email setup
Needs: SMTP server (Gmail: `smtp.gmail.com`, port 587), your address, an
[app password](https://support.google.com/accounts/answer/185833), and the
address to send alerts to. Run `python -m xrplarb alerts-setup email`.

### Watchdog setup
Create a free check at healthchecks.io with a 5-minute period. Run
`python -m xrplarb alerts-setup watchdog` and paste its ping URL (not secret). Connect healthchecks.io
to your email/Telegram in its settings.

Test everything with: `python -m xrplarb alerts-test`

## Kill switch: two ways from your phone

1. **Dashboard:** scroll to *Controls*, type `STOP`, press **KILL SWITCH**.
2. **Telegram:** send `/kill 1234` (your PIN).

What happens: every bot stops trading, waits for any in-flight trade to settle
(seconds), then sells every token it holds back to XRP (accepting up to 25%
below current price if that's what it takes to get out). You get an alert
listing what was sold and the final XRP balance of each bot. Nothing restarts
until you press **Resume** (or send `/resume <PIN>`).
