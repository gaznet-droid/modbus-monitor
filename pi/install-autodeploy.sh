#!/bin/sh
# One-time setup of auto-deploy. Run on the Pi from a checkout of main:
#     sudo sh pi/install-autodeploy.sh
# Also re-run it by hand whenever autodeploy.sh or a systemd unit changes.
set -eu
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo on the Pi"; exit 1; }
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
command -v git >/dev/null || apt-get install -y --no-install-recommends git
for tool in rsync curl flock timeout runuser; do
    command -v "$tool" >/dev/null || { echo "Missing $tool"; exit 1; }
done
install -m 0755 "$ROOT/pi/autodeploy.sh" /usr/local/sbin/modpi-autodeploy
# Only the deploy units. The app units are left exactly as they are on the Pi.
for unit in modpi-deploy.service modpi-deploy.timer; do
    install -m 0644 "$ROOT/pi/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/modpi-deploy.service /etc/systemd/system/modpi-deploy.timer
# Clear a blocked marker so the next run re-checks against what is now installed.
rm -f /var/lib/modpi-deploy/blocked
systemctl enable --now modpi-deploy.timer
echo "Auto-deploy enabled. First run in about 3 minutes, then every 5."
echo "Watch it:  journalctl -u modpi-deploy -f"
echo "Run now:   sudo systemctl start modpi-deploy"
