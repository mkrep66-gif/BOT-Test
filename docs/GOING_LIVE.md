# Going live (only after your written approval)

Nothing in this file happens until you say so in writing. Every step that
costs money is marked 💲 and I'll stop and ask before it.

## Step 1: Rent a small server 💲

What we need: 1–2 CPU, 2 GB RAM, 20+ GB disk, Ubuntu 24.04. The bots are
light; they use public XRP Ledger servers, not their own.

| Provider | Plan | Approx. price* |
|---|---|---|
| Hetzner Cloud | CX22 (2 vCPU, 4 GB) | ~€4–5 / month |
| DigitalOcean | Basic Droplet (1 vCPU, 2 GB) | ~$12 / month ($6 for 1 GB also works) |
| Vultr | Regular Cloud Compute (1 vCPU, 2 GB) | ~$10 / month |

\*Prices change. I'll confirm the current price on the checkout page with you
before you pay. Expect **about $5–12/month**, billed monthly, cancel any time.
No other paid services are required (Telegram, Tailscale personal and
healthchecks.io free tiers are enough).

How to do it (Hetzner example):
1. Sign up at hetzner.com/cloud, add a payment method. 💲
2. *Add Server* → location: any (e.g. Falkenstein or Ashburn) → image **Ubuntu 24.04** → type **CX22**.
3. Under *SSH keys*, I'll give you a one-line command to make a key on your computer and paste the public half there.
4. Create. Send me the server's IP address (that's not secret).

## Step 2: Install

I'll log in and run `sudo bash deploy/install.sh`. It:
- creates a locked-down `xrplarb` user that owns the bots,
- turns on the firewall (only SSH allowed in) and automatic security updates,
- installs the code and the three background services (bots, dashboard, watch).

## Step 3: Phone access via Tailscale (free)

Rather than putting the dashboard on the open internet, we use **Tailscale**,
a free private network between *your* devices:
1. Install the Tailscale app on your phone and sign in.
2. I install Tailscale on the server and you approve it in the app.
3. The dashboard is then at `http://<server-name>:8080` **only from your phone**.
   It's still password-protected on top.

## Step 4: Mainnet watch-only week

`systemctl enable --now xrplarb-watch`. No keys, no money. After ≥ 7 days I
send you the report with a plain recommendation.

## Step 5: First live bot ✋💲 (needs your written approval)

Only if the watch report looks worthwhile and you say so in writing:

1. Settings: `network: mainnet`, `allow_mainnet_trading: true`, `num_bots: 1`,
   `working_balance_xrp: 20`, `vault_address.mainnet: <your own wallet address>`.
2. Create **one** mainnet wallet: `wallets-create --network mainnet --count 1 --i-have-written-approval`.
3. **Back up the key** (SECRETS_AND_BACKUP.md) before funding.
4. Re-review the tokens on mainnet; you confirm the approved list in writing.
5. **You** send XRP to the bot address from your own wallet. Amount needed:
   20 XRP working balance + 1 XRP base reserve + 0.2 XRP × number of tokens + ~1 XRP
   for fees. For 10 tokens: about **24 XRP**. (The reserve XRP is locked, not spent;
   it comes back if the wallet is closed.)
6. Trust lines, start the bot, watch the dashboard together.

## Step 6: Scaling ✋

Only with your OK, one change at a time, after at least a week of results:
raise the working balance toward 100 XRP, then (only if the "would more bots
help?" numbers say so) add a second bot.

## Stopping for good

Kill switch → everything goes back to XRP → send the XRP to your own wallet →
cancel the server. I can walk you through closing the bot wallets to recover
the base reserve too.
