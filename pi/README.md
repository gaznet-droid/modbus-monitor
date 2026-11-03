# ModPi preparation — 28 October 2026

Target: Raspberry Pi 3 Model A+ (512 MB, Wi-Fi only), Raspberry Pi OS Lite **32-bit**.
(Originally prepared for a Pi Zero W; the installer is unchanged.)
Source: ewelink-monitoring commit 3b34786, isolated raspberry-pi branch/worktree.
All application runtime code uses the Python standard library. Do not install
the root requirements.txt: it is the old FastAPI deployment, unnecessary here.

## Current state
- Mac worktree: /Users/garethbrown/modbus-pi.
- 3 November: Pi 3A+ running Raspberry Pi OS Lite 32-bit (Trixie, Python 3.13),
  hostname modpi, operator account admin (SSH key only). Installed with
  install.sh; modpi.service enabled, collectors off, survives reboot, tests pass.
- Wi-Fi profiles: Clock Mill Barn (staging) and dad-bthub (untested until on site).
- Maintenance access: Tailscale (`ssh modpi-ts` from the Mac), key expiry disabled.
- Pi adapter uses existing dashboard, SQLite schemas, retention and APIs.
- Default flags disable Modbus and eWeLink collectors. No Tuya cloud worker.
- No production source, service, tunnel, credentials or database was changed.
- Local Juwai access is still unproven. Do not promise working local collection.

## Image the card
First boot is on Gareth's network, SSID: Clock Mill Barn. Dad's SSID is pending.
Add and test Dad's Wi-Fi profile before transferring the Pi.
Use the current official Raspberry Pi Imager, choose Raspberry Pi Zero W and
Raspberry Pi OS Lite (32-bit). Select only the verified 31.5 GB microSD.
Imaging erases this card. Do not select the attached 2 TB SLAVE drive.
Set hostname modpi, UK Wi-Fi country, Europe/London timezone, operator account,
SSH and the Wi-Fi network available at FIRST BOOT (2.4 GHz for Zero W).
Prefer the Mac's public SSH key. Never copy private keys into the image.
Obtain Wi-Fi settings and operator choices before imaging; enter passwords
privately in Imager. The FAT label may become bootfs after imaging.

## First boot and installation
Insert into the powered-off Pi, power up with its proper supply, then SSH to
the configured user at modpi.local. Confirm the board, OS and network.
Copy this source checkout to the operator's home on the Pi, excluding .git,
venv, caches and private files. On the Pi run:

    sudo sh ~/modbus-pi/pi/install.sh

Installer copies source to /opt/modpi, creates unprivileged modpi service user,
private state directories and systemd unit. It does not start or enable service.
History: /var/lib/modpi/data. Private state: /var/lib/modpi/private.
Service runs with strict filesystem protections and a 15-second restart delay.
Storage guard requires writable root ext4 and at least 64 MiB free.
This is Pi microSD storage; the router's dedicated USB serial guard is not used.
Clock must be synchronised before collecting timestamped readings.

Start the empty, local dashboard after checks:

    sudo systemctl enable --now modpi.service
    sudo systemctl status modpi.service
    sudo journalctl -u modpi.service -n 30

From the Mac, use the chosen Pi account:

    ssh -L 8097:127.0.0.1:8096 USER@modpi.local

Open http://127.0.0.1:8097. Unavailable readings and /health HTTP 503 are expected
until meter collection is enabled. No demo readings or old history are fabricated.

## Activation and migration still required
1. Verify the real Modbus endpoint and port from Dad's LAN; do not assume the
   router's public endpoint is the correct local route.
2. Decide and perform a coordinated handover of each collector. Avoid duplicate
   eWeLink cloud polling and separate processes rotating the same OAuth tokens.
3. Stop the old cloud collector before securely migrating its private config,
   tokens, quota counters, schedule and locks. Keep existing budget/schedule state;
   do not reset quota accounting. Destination files must belong to modpi, mode 0600.
   Pi eWeLink base config path is /var/lib/modpi/private/ewelink.json.
   Existing adapter suffixes (.tokens, .budget, .schedule, etc.) remain in use.
4. Copy SQLite history with a consistent SQLite backup or stopped writers; copying
   a live database file alone is not a safe history migration.
5. Change only verified flags/endpoint in /etc/modpi/monitor.json, then restart
   modpi.service. No Tuya cloud fallback is automatically activated.
6. Verify real readings, timestamps, persistence, outage recovery and reboot.
7. Only then plan the existing public tunnel's origin handover and rollback.
   Do not modify unrelated Clock Mill networking or publish an extra deployment.

## Source validation on Mac
    cd /Users/garethbrown/modbus-pi
    PYTHONPATH=router:pi python3 -m unittest discover -s tests -v
    python3 pi/run.py --config pi/monitor.example.json --check-config
    sh -n pi/install.sh
    node --check router/web/monitor.js
    node --check router/web/ewelink.js

Mac tests use fixtures and mocks. They do not prove Pi boot, Linux storage,
systemd behavior, real Juwai compatibility or successful credential migration.
