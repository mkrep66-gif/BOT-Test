#!/usr/bin/env bash
# One-time server setup (Ubuntu 24.04). Run as root:  sudo bash deploy/install.sh
# Creates a dedicated 'xrplarb' user, installs the code in /opt/xrplarb,
# and registers three background services (they start only when you say so).
set -euo pipefail

APP=/opt/xrplarb
apt-get update -y
apt-get install -y python3 python3-venv git rsync ufw unattended-upgrades

# Locked-down firewall: only SSH in. The dashboard is reached via Tailscale.
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw --force enable

# Automatic security updates
dpkg-reconfigure -f noninteractive unattended-upgrades

id xrplarb >/dev/null 2>&1 || useradd --system --create-home --home-dir /home/xrplarb --shell /usr/sbin/nologin xrplarb
mkdir -p "$APP"
rsync -a --exclude secrets --exclude data --exclude .git ./ "$APP"/
mkdir -p "$APP/secrets" "$APP/data" "$APP/reports"
python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install --upgrade pip
"$APP/.venv/bin/pip" install -r "$APP/requirements.txt"
chown -R xrplarb:xrplarb "$APP"
chmod 700 "$APP/secrets"

cp deploy/xrplarb-*.service /etc/systemd/system/
systemctl daemon-reload
echo
echo "Installed. Nothing is running yet. Next steps are in docs/GOING_LIVE.md."
