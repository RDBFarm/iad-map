#!/bin/bash
# Installs the IAD map publisher on the ADS-B Pi. Run on the Pi as:
#   curl -fsSL https://raw.githubusercontent.com/RDBFarm/iad-map/main/pi/install.sh | sudo bash
# Safe to run again: it replaces the programs and keeps the recorded data and key.
# It does not touch readsb, tar1090 or the ADS-B Exchange feed.
set -euo pipefail

SRC=https://raw.githubusercontent.com/RDBFarm/iad-map/main/pi
HOME_DIR=/var/lib/iad-map
USER_NAME=iadmap

if [ "$(id -u)" -ne 0 ]; then echo "Please run with sudo."; exit 1; fi

echo "== Checking what's needed"
command -v python3 >/dev/null || { echo "python3 is missing; stopping."; exit 1; }
if ! command -v git >/dev/null; then
  echo "   installing git"; apt-get update -qq && apt-get install -y -qq git
fi
[ -r /run/readsb/aircraft.json ] || echo "   note: /run/readsb/aircraft.json not readable right now"

echo "== Creating the $USER_NAME account and $HOME_DIR"
id "$USER_NAME" >/dev/null 2>&1 || useradd --system --home-dir "$HOME_DIR" --shell /usr/sbin/nologin "$USER_NAME"
mkdir -p "$HOME_DIR/points" /opt/iad-map
chown -R "$USER_NAME:$USER_NAME" "$HOME_DIR"

echo "== Downloading the programs"
for f in collector.py publish.py; do
  curl -fsSL "$SRC/$f" -o "/opt/iad-map/$f"
done
chmod 755 /opt/iad-map/*.py

echo "== Setting up the services"
cat > /etc/systemd/system/iad-map-collector.service <<UNIT
[Unit]
Description=IAD map: record aircraft positions
After=readsb.service

[Service]
User=$USER_NAME
ExecStart=/usr/bin/python3 /opt/iad-map/collector.py
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/iad-map-publish.service <<UNIT
[Unit]
Description=IAD map: push the last 24 hours to GitHub
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$USER_NAME
ExecStart=/usr/bin/python3 /opt/iad-map/publish.py
UNIT

cat > /etc/systemd/system/iad-map-publish.timer <<UNIT
[Unit]
Description=IAD map: publish every 15 minutes

[Timer]
OnCalendar=*:0/15
Persistent=true

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl enable --now iad-map-collector.service
systemctl enable iad-map-publish.timer

if [ ! -f "$HOME_DIR/deploy_key" ]; then
  sudo -u "$USER_NAME" ssh-keygen -q -t ed25519 -N "" -C "rdbf-adsb-pi" -f "$HOME_DIR/deploy_key"
fi

echo
echo "=================================================================="
echo " Recording has started. One step left: let this Pi write to GitHub."
echo " Copy the whole line below (it starts with ssh-ed25519):"
echo
cat "$HOME_DIR/deploy_key.pub"
echo
echo " Then run:  sudo systemctl start iad-map-publish.timer"
echo "=================================================================="
