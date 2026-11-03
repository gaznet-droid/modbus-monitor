#!/bin/sh
# Run on the Pi after Raspberry Pi OS Lite 32-bit has booted.
set -eu
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo on the Pi"; exit 1; }
[ "$(uname -s)" = Linux ] || { echo "Linux required"; exit 1; }
[ "$(dpkg --print-architecture)" = armhf ] || { echo "32-bit armhf OS required"; exit 1; }
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
[ "$ROOT" != /opt/modpi ] || { echo "Install from a source checkout outside /opt/modpi"; exit 1; }
python3 -c 'import sqlite3, zoneinfo'
if systemctl is-active --quiet modpi.service; then
    echo "Stop modpi.service before updating its source"; exit 1
fi
getent passwd modpi >/dev/null || useradd --system --home-dir /var/lib/modpi --shell /usr/sbin/nologin modpi
install -d -m 0755 /opt/modpi /opt/modpi/router /opt/modpi/pi /etc/modpi
cp -R "$ROOT/router/." /opt/modpi/router/
install -m 0644 "$ROOT/pi/run.py" "$ROOT/pi/storage.py" "$ROOT/pi/tuya_run.py" /opt/modpi/pi/
chown -R root:root /opt/modpi
chmod -R go-w /opt/modpi
install -d -o modpi -g modpi -m 0700 /var/lib/modpi /var/lib/modpi/data /var/lib/modpi/private
if [ ! -e /etc/modpi/monitor.json ]; then
    install -o root -g modpi -m 0640 "$ROOT/pi/monitor.example.json" /etc/modpi/monitor.json
fi
install -m 0644 "$ROOT/pi/modpi.service" /etc/systemd/system/modpi.service
install -m 0644 "$ROOT/pi/modpi-tuya.service" /etc/systemd/system/modpi-tuya.service
install -m 0644 "$ROOT/pi/modpi-tunnel.service" /etc/systemd/system/modpi-tunnel.service
install -d -m 0700 /etc/cloudflared
systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/modpi.service /etc/systemd/system/modpi-tuya.service
if command -v cloudflared >/dev/null; then systemd-analyze verify /etc/systemd/system/modpi-tunnel.service; fi
runuser -u modpi -- python3 /opt/modpi/pi/run.py --check-config
echo "Installed. Service remains stopped until first-boot validation."
echo "Start dashboard: sudo systemctl enable --now modpi.service"
