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
rsync -a --exclude secrets --exclude data --exclude .git --exclude config ./ "$APP"/
# Settings, token approvals and the block list belong to this server: copy
# them on the first install only, never over the server's own on updates.
mkdir -p "$APP/config"
rsync -a --ignore-existing ./config/ "$APP/config/"
mkdir -p "$APP/secrets" "$APP/data" "$APP/reports"
python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install --upgrade pip
"$APP/.venv/bin/pip" install -r "$APP/requirements.txt"
chown -R xrplarb:xrplarb "$APP"
chmod 700 "$APP/secrets"

cp deploy/xrplarb-*.service /etc/systemd/system/
install -m 755 deploy/xrplarb-ctl /usr/local/bin/xrplarb-ctl
systemctl daemon-reload
# On an update, restart whatever is running so the new code takes effect.
systemctl try-restart xrplarb-bots xrplarb-labnoise xrplarb-dashboard xrplarb-watch 2>/dev/null || true
echo
echo "Installed. (Anything already running was restarted on the new code; nothing new was started.)"
