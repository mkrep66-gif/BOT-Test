#!/usr/bin/env bash
# Fresh Testnet setup on the server. Run once, as root, after deploy/install.sh:
#   sudo bash deploy/testnet-setup.sh
# Creates new Testnet bot wallets + vault (free faucet XRP), new practice
# tokens and pools, and the token safety report. Nothing starts trading:
# the owner approves the tokens first.
set -euo pipefail

APP=/opt/xrplarb
run() { (cd "$APP" && sudo -u xrplarb "$APP/.venv/bin/python" -m xrplarb "$@"); }

if [ -s "$APP/secrets/secrets.yaml" ]; then
    echo "Wallets already exist on this server - not creating new ones."
    echo "Show them with:  cd $APP && sudo -u xrplarb .venv/bin/python -m xrplarb wallets-show --network testnet"
    exit 1
fi

# The practice tokens and vault in the repo were made in the build
# environment; their keys stayed there, so this server makes its own.
rm -f "$APP/config/approved_tokens.testnet.yaml" "$APP/config/markets.testnet.yaml"
sed -i 's|^  testnet: "r[^"]*"|  testnet: ""|' "$APP/config/settings.yaml"

echo "== Checking the Testnet connection"
run check-network --network testnet

echo "== Creating 5 bot wallets + a vault (takes a few minutes)"
out=$(run wallets-create --network testnet --count 5 --vault | tee /dev/stderr)
vault=$(printf '%s\n' "$out" | awk '/^vault:/{print $2}')
if [ -n "$vault" ]; then
    sed -i "s|^  testnet: \"\"|  testnet: \"$vault\"|" "$APP/config/settings.yaml"
    echo "Vault address saved in settings: $vault"
fi

echo "== Creating practice tokens and pools (takes a few minutes)"
run lab-setup

echo "== Token safety review"
run tokens-review --network testnet --min-liquidity 20

echo "== Security check"
run security-check

echo
echo "Done. Nothing is trading yet. Send Claude the token list above for your approval."
