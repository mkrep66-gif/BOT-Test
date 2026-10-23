#!/usr/bin/env bash
# Lock the server down. Run once as root:  sudo bash deploy/harden.sh
#
#  * a personal admin login "botadmin" (SSH key only) instead of root
#  * no root logins, no password logins at all - keys only
#  * fail2ban: blocks addresses that keep failing to log in
#  * security updates installed automatically every day (with a reboot at
#    ~04:30 UTC / 00:30 New York only when an update needs one; the bots
#    start again by themselves)
#  * the xrplarb-ctl helper behind the Start / Stop / Status buttons
#
# Safe to run again. Refuses to run if no SSH key is installed yet, so it
# can't lock you out.
set -euo pipefail

ADMIN=botadmin
SRC_KEYS=/root/.ssh/authorized_keys
HERE="$(cd "$(dirname "$0")" && pwd)"

if [ "$(id -u)" -ne 0 ]; then echo "Run as root (sudo bash deploy/harden.sh)"; exit 1; fi
if ! grep -qsE '^(ssh-ed25519|ssh-rsa|ecdsa-sha2)' "$SRC_KEYS" && ! grep -qsE '^(ssh-ed25519|ssh-rsa|ecdsa-sha2)' "/home/$ADMIN/.ssh/authorized_keys"; then
    echo "No SSH key installed yet - refusing to turn off password logins (you'd be locked out)."
    echo "Add your public key first (see the instructions in chat)."
    exit 1
fi

echo "== Admin user $ADMIN (SSH key only, can use sudo)"
id "$ADMIN" >/dev/null 2>&1 || adduser --disabled-password --gecos "" "$ADMIN"
usermod -aG sudo "$ADMIN"
passwd -l "$ADMIN" >/dev/null           # no password exists, so none can be guessed
echo "$ADMIN ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/90-$ADMIN
chmod 440 /etc/sudoers.d/90-$ADMIN
visudo -cf /etc/sudoers.d/90-$ADMIN >/dev/null
install -d -m 700 -o "$ADMIN" -g "$ADMIN" "/home/$ADMIN/.ssh"
touch "/home/$ADMIN/.ssh/authorized_keys"
if [ -s "$SRC_KEYS" ]; then
    # merge root's keys in (no duplicates)
    sort -u "/home/$ADMIN/.ssh/authorized_keys" "$SRC_KEYS" > "/home/$ADMIN/.ssh/authorized_keys.new"
    mv "/home/$ADMIN/.ssh/authorized_keys.new" "/home/$ADMIN/.ssh/authorized_keys"
fi
chown "$ADMIN:$ADMIN" "/home/$ADMIN/.ssh/authorized_keys"
chmod 600 "/home/$ADMIN/.ssh/authorized_keys"

echo "== SSH: keys only, no root, no passwords"
cat > /etc/ssh/sshd_config.d/00-xrplarb-hardening.conf <<EOF
# Managed by deploy/harden.sh (read before the cloud defaults, so it wins)
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
AuthenticationMethods publickey
PermitEmptyPasswords no
MaxAuthTries 3
LoginGraceTime 30
X11Forwarding no
# local forwarding stays on: it's how the dashboard is reached privately
AllowTcpForwarding local
AllowAgentForwarding no
AllowUsers $ADMIN
EOF
sshd -t                                   # stop here if the config is invalid
systemctl reload ssh 2>/dev/null || systemctl restart ssh

echo "== fail2ban"
DEBIAN_FRONTEND=noninteractive apt-get install -y fail2ban >/dev/null
cat > /etc/fail2ban/jail.d/xrplarb-sshd.local <<'EOF'
[sshd]
enabled  = true
backend  = systemd
maxretry = 5
findtime = 10m
bantime  = 1h
EOF
systemctl enable --now fail2ban >/dev/null
systemctl restart fail2ban

echo "== Daily automatic security updates"
DEBIAN_FRONTEND=noninteractive apt-get install -y unattended-upgrades >/dev/null
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF
cat > /etc/apt/apt.conf.d/52xrplarb-reboot <<'EOF'
// Reboot for kernel/security updates only when needed, in the quiet hours.
// The bots are systemd services and start again by themselves.
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "04:30";
EOF
systemctl enable --now unattended-upgrades >/dev/null

echo "== Start/Stop/Status helper"
install -m 755 "$HERE/xrplarb-ctl" /usr/local/bin/xrplarb-ctl

echo
echo "Done. From now on log in as:  ssh $ADMIN@<server-ip>"
echo "Root and password logins are switched off. KEEP THIS WINDOW OPEN until you've"
echo "checked in a second window that the new login works."
