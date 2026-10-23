#!/usr/bin/env bash
# Bring in new mainnet candidates from the repo, then approve every candidate whose
# safety review PASSED (OK or CAUTION). Never approves a NOT RECOMMENDED token.
# Only run this after the owner has approved in writing; quote them in the note.
#   sudo bash deploy/approve-reviewed.sh "Owner approved in chat <date>: <their words>"
set -euo pipefail
NOTE="${1:?give a note quoting the owner's written approval}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
echo "Code version: $(git -C "$REPO" log -1 --format="%h %s" | cut -c1-70)"
APP=/opt/xrplarb
cd "$APP"
.venv/bin/python -m xrplarb tokens-merge "$REPO/config/approved_tokens.mainnet.yaml" \
    --markets "$REPO/config/markets.mainnet.yaml" --network mainnet
chown -R xrplarb:xrplarb config
keys=$(sudo -u xrplarb .venv/bin/python -c "
from xrplarb.config import load_token_entries as l
print(' '.join(e['currency'] + '.' + e['issuer'] for e in l('mainnet') if e.get('verdict') in ('OK', 'CAUTION')))")
n=0
for k in $keys; do
    sudo -u xrplarb .venv/bin/python -m xrplarb tokens-approve "$k" --network mainnet --note "$NOTE" >/dev/null && n=$((n+1))
done
sudo -u xrplarb .venv/bin/python -c "
from xrplarb.config import load_token_entries as l
e = l('mainnet')
ok = [x['name'] for x in e if x.get('approved')]
print(f'{len(ok)} approved of {len(e)} on the mainnet list:', ', '.join(ok))
print('Not approved (failed review):', ', '.join(x['name'] for x in e if not x.get('approved')))"
grep -q '^allow_mainnet_trading: false' config/settings.yaml && echo "Mainnet trading: OFF (unchanged)"
