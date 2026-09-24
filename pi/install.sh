#!/bin/bash
# Installs the IAD map publisher on the ADS-B Pi. Run on the Pi as:
#   curl -fsSL https://raw.githubusercontent.com/RDBFarm/iad-map/main/pi/install.sh | sudo bash
# Safe to run again: it replaces the programs and keeps the recorded data and key.
# It does not touch readsb, tar1090 or the ADS-B Exchange feed.
set -euo pipefail

SRC=https://raw.githubusercontent.com/RDBFarm/iad-map/main/pi
HOME_DIR=/var/lib/iad-map          # the GitHub key only; small
DATA_DIR=/mnt/flightdata/iad-map   # the log and the publish copy, on the USB drive
USER_NAME=iadmap

if [ "$(id -u)" -ne 0 ]; then echo "Please run with sudo."; exit 1; fi

echo "== Checking what's needed"
command -v python3 >/dev/null || { echo "python3 is missing; stopping."; exit 1; }
if ! command -v git >/dev/null; then
  echo "   installing git"; apt-get update -qq && apt-get install -y -qq git
fi
[ -r /run/readsb/aircraft.json ] || echo "   note: /run/readsb/aircraft.json not readable right now"
if ! mountpoint -q /mnt/flightdata; then
  echo "The USB drive is not mounted at /mnt/flightdata; stopping so nothing is written to the SD card."
  exit 1
fi

echo "== Creating the $USER_NAME account, $HOME_DIR and $DATA_DIR"
id "$USER_NAME" >/dev/null 2>&1 || useradd --system --home-dir "$HOME_DIR" --shell /usr/sbin/nologin "$USER_NAME"
mkdir -p "$HOME_DIR" "$DATA_DIR" /opt/iad-map
chown -R "$USER_NAME:$USER_NAME" "$HOME_DIR" "$DATA_DIR"

echo "== Downloading the programs"
for f in collector.py publish.py proximity.py farm.py alerts.py aircraft_lookup.py tracks.py aircraft_types.json; do
  curl -fsSL "$SRC/$f" -o "/opt/iad-map/$f"
done
chmod 755 /opt/iad-map/*.py; chmod 644 /opt/iad-map/aircraft_types.json

echo "== Setting up the services"
cat > /etc/systemd/system/iad-map-collector.service <<UNIT
[Unit]
Description=IAD map: log everything the receiver hears
After=readsb.service
RequiresMountsFor=/mnt/flightdata

[Service]
User=$USER_NAME
ExecStart=/usr/bin/python3 /opt/iad-map/collector.py
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/iad-map-alerts.service <<UNIT
[Unit]
Description=IAD map: push 7500/7600/7700 emergencies to the owner's phone
After=readsb.service network-online.target
Wants=network-online.target
RequiresMountsFor=/mnt/flightdata

[Service]
User=$USER_NAME
ExecStart=/usr/bin/python3 /opt/iad-map/alerts.py
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
RequiresMountsFor=/mnt/flightdata

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

cat > /etc/systemd/system/iad-map-aircraft-db.service <<UNIT
[Unit]
Description=IAD map: refresh the aircraft type lookup (tar1090-db)
After=network-online.target
Wants=network-online.target
RequiresMountsFor=/mnt/flightdata

[Service]
Type=oneshot
User=$USER_NAME
ExecStart=/usr/bin/python3 /opt/iad-map/aircraft_lookup.py --build
UNIT

cat > /etc/systemd/system/iad-map-aircraft-db.timer <<UNIT
[Unit]
Description=IAD map: refresh the aircraft type lookup monthly

[Timer]
OnCalendar=monthly
Persistent=true

[Install]
WantedBy=timers.target
UNIT

echo "== Building the aircraft type lookup (downloads about 8 MB)"
sudo -u "$USER_NAME" python3 /opt/iad-map/aircraft_lookup.py --build || echo "   lookup build failed; types will be missing until it succeeds"

systemctl daemon-reload
systemctl enable --now iad-map-collector.service iad-map-aircraft-db.timer
systemctl enable iad-map-publish.timer iad-map-alerts.service
# On a re-run, restart what is already running so it picks up the new programs.
systemctl try-restart iad-map-collector.service iad-map-alerts.service

if [ ! -f "$HOME_DIR/deploy_key" ]; then
  sudo -u "$USER_NAME" ssh-keygen -q -t ed25519 -N "" -C "rdbf-adsb-pi" -f "$HOME_DIR/deploy_key"
fi

echo
echo "=================================================================="
echo " Logging to $DATA_DIR/flights.db has started. One step left: let this Pi write to GitHub."
echo " Copy the whole line below (it starts with ssh-ed25519):"
echo
cat "$HOME_DIR/deploy_key.pub"
echo
echo " Then run:  sudo systemctl start iad-map-publish.timer iad-map-alerts.service"
echo " To test the phone alert:  sudo -u $USER_NAME python3 /opt/iad-map/alerts.py --test"
echo "=================================================================="
