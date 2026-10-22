# Secret keys: where they live and how to back them up

## Where they live

- Each bot wallet's secret key ("seed", starts with `s`) is written **only** to
  `secrets/secrets.yaml`.
- That folder is readable by your server user only (folder `700`, file `600`).
  The bots **refuse to start** if anyone else can read it.
- The file is excluded from git, so it can never be uploaded with the code.
- Every log line, alert, database entry and dashboard page passes through a
  filter that removes any known key and anything shaped like a key.
- The dashboard shows wallet **addresses** (public, start with `r`), never keys.
- The dashboard password isn't stored. Only a scrambled one-way hash is.

I (Claude) will never print a key in chat. `python -m xrplarb secrets-show`
lists what's stored without showing secrets.

## Testnet keys

Testnet XRP is free and worthless. Losing these keys doesn't matter; we can make new ones.

## Mainnet keys: back up before funding

Do this **before** sending any real XRP to a bot wallet:

1. On the server, make an encrypted backup:
   ```
   sudo -u xrplarb /opt/xrplarb/.venv/bin/python -m xrplarb backup-secrets --out /home/xrplarb/xrplarb-keys.enc
   ```
   Pick a long passphrase (e.g. 5–6 random words). Write the passphrase on paper.
2. Copy `xrplarb-keys.enc` to **two** places you control, e.g. a USB stick kept
   at home and your password manager's secure file storage. The file is useless
   without the passphrase.
3. Keep the paper passphrase **separate** from the USB stick.
4. Test it once: `python -m xrplarb restore-secrets <file>` on a spare machine (or ask me to walk you through it).

**Never:** email the key file, paste keys in chat, photograph them, put the
unencrypted `secrets.yaml` in Google Drive/iCloud/Dropbox, or share the server login.

## Limiting the damage if a key ever leaks

- Bot wallets hold at most 100 XRP (20 XRP at mainnet start) by design.
- Profits above the cap are swept to your **vault**: a wallet whose key is
  **not** on the server (e.g. your Xaman wallet or a hardware wallet). So the
  server only ever holds working money.
