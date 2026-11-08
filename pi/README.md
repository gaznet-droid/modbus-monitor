# ModPi — Raspberry Pi production host

Target: Raspberry Pi 3 Model A+ (512 MB, Wi-Fi only), Raspberry Pi OS Lite **32-bit**.
(Originally prepared for a Pi Zero W on 28 October; the installer is unchanged.)
Source: main branch (raspberry-pi was merged into main on 6 Nov 2026).
All application runtime code uses the Python standard library. Do not install
the root requirements.txt: it is the old FastAPI deployment, unnecessary here.

## Current state (4 November 2026)
- PRODUCTION RUNS ON THE PI. modbus.clockmillbarn.uk is served by modpi-tunnel; the
  Mac, Slave and the RUTX12 are no longer involved. Full detail, including state not
  in git: docs/handoff-2026-11-04.md.
- Pi 3A+ on its own power supply at Gareth's house, Raspberry Pi OS Lite 32-bit
  (Trixie, Python 3.13), hostname modpi, operator account admin (SSH key only).
- Units enabled and running: modpi (dashboard, meter and eWeLink collectors),
  modpi-tuya (Tuya cloud collector, no auto-restart by design) and modpi-tunnel.
- Meter: read over Dad's public IP and router port-forward (meter_host updated at 09:39
  on 4 November after his IP changed at 01:18; the value lives only in
  /etc/modpi/monitor.json, never in this public repo). Moves to a LAN address on site.
- Wi-Fi profiles: Clock Mill Barn (current) and dad-bthub (untested until on site).
- Maintenance access: Tailscale (`ssh modpi-ts` from the Mac), key expiry disabled.
- Tuya local keys and MACs for both Juwai meters: /var/lib/modpi/private/tuya-local-keys.json,
  with a second copy on the Mac in ~/Developer/modbus-private/. Re-pairing changes them.
- Slave's ModbusRecovery copy is untouched and is the rollback copy. Do NOT run
  Start-Modbus.command on Slave: it would start duplicate collectors and rotate the
  same eWeLink tokens. Rollback = stop the three Pi units, copy
  /var/lib/modpi/private/ewelink.json.* back to Slave's config, then start the supervisor.
- monitor.example.json still ships with collectors disabled; production flags live only
  in /etc/modpi/monitor.json on the Pi.
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

## Auto-deploy from GitHub
GitHub `main` is the source of truth. Push from any device and it goes live within seconds,
with no Mac needed.

Main trigger (since 7 Nov): a signed GitHub push webhook (JSON, push events only) to
https://modbus.clockmillbarn.uk/api/deploy-hook. The site checks the HMAC signature against
/var/lib/modpi/private/deploy-hook.secret (owner modpi:modpi, mode 0600, never committed or
printed), then touches /var/lib/modpi/deploy-request; modpi-deploy.path starts the deploy.
Only pushes to main queue a deploy, and the hook can only make the Pi pull main. Tested on
7 Nov: a push went live about 3 seconds later with the timer paused. Results show in the
modpi journal as `deploy-hook:` lines (`ping ok`, `push to main queued`, or a refusal).
To rotate the secret: set a new one in the repo's webhook settings, write it to that file
with the same owner and mode (no restart needed), then Redeliver the ping on GitHub.

Backup: modpi-deploy.timer checks main once a day at 03:30 (up to 10 minutes later, and
on the next boot if the Pi was off), so a missed webhook delivery goes live by morning.
A check with no new commit is one small anonymous, read-only fetch.

What `/usr/local/sbin/modpi-autodeploy` (pi/autodeploy.sh) does with a new commit:
1. Pulls main anonymously over HTTPS (public repo, no credentials on the Pi).
2. Docs-only commits are just recorded.
3. Runs the full test suite as `nobody`, then `run.py --check-config`. Failure = nothing deployed.
4. Compares the new app files with /opt/modpi. Page-only changes (router/web) go live
   with no restart. Python changes back up /var/lib/modpi/private, then restart modpi;
   modpi-tuya restarts only if Tuya or storage code changed and it was already running.
5. Checks the dashboard answers on port 8096 within a minute. If not, it restores the
   previous copy and restarts again.
6. A failed commit is not retried. Push a fix and the next run picks it up.
7. If main's systemd units or autodeploy.sh differ from those installed on the Pi, the
   deploy is BLOCKED until they are applied by hand over Tailscale SSH: copy main to
   the Pi and run `sudo sh pi/install-autodeploy.sh` (app units: copy them to
   /etc/systemd/system and `sudo systemctl daemon-reload` first).

Check what is live from anywhere: https://modbus.clockmillbarn.uk/version
(commit, time and subject; also shows FAILED, ROLLED BACK or BLOCKED).
On the Pi: `journalctl -u modpi-deploy`, state in /var/lib/modpi-deploy.
Run now: `sudo systemctl start modpi-deploy`. Pause: `sudo systemctl stop modpi-deploy.timer`.
Journal noise: each empty check adds a couple of systemd lines; that is expected.

One-time setup on the Pi (from a checkout of main): `sudo sh pi/install-autodeploy.sh`.

## Login (built 8 Nov, switched off until set up)
The whole site can sit behind a login for allowlisted people only (Gareth and Dad). Sign-in is
Google (allowlisted emails) or a passkey (Face ID, Touch ID, Android fingerprint). The footer's
Admin link (shown only when login is on) creates one-time sign-up links, valid 24 hours, that add a
passkey on a new device, and lists passkeys and signed-in devices so either person can remove one.
Sessions last 180 days from the last visit. Code: router/auth.py; pages: router/web/login.html,
admin.html, auth.js.

Switched on by a private settings file, never committed:
/var/lib/modpi/private/auth.json (owner modpi:modpi, mode 0600)

    {"enabled": true, "origin": "https://modbus.clockmillbarn.uk",
     "users": {"gareth": {"email": "...", "name": "Gareth"}, "dad": {"email": "...", "name": "Dad"}},
     "google": {"client_id": "...", "client_secret": "..."}}

The app re-reads it on change; no restart needed. Sessions, passkeys and links live in
/var/lib/modpi/private/auth.sqlite3 (only token hashes are stored). If the file exists but is
invalid, the site stays locked rather than open. Without login, these still answer: /login,
/enrol, the Google and passkey sign-in endpoints, /health, /version, the eWeLink OAuth callback,
the GitHub deploy webhook, icons and the two public JavaScript files.

Locked out? Over Tailscale SSH set "enabled": false in auth.json (the site opens again at once),
or delete a row from auth.sqlite3. Passkeys need python3-cryptography (Debian package, installed).

Google client (one-off, in Gareth's Google account): Google Cloud console, new project, OAuth
consent screen (External, app name "Modbus monitor", add Gareth's and Dad's emails as test users),
then Credentials, Create OAuth client ID, Web application, authorised redirect URI
https://modbus.clockmillbarn.uk/auth/google/callback. Copy the client ID and secret into auth.json.

## Premium Bonds (Oscar's tracker)
Oscar's Premium Bond Tracker runs on the Pi as bondtracker.service (~admin/bonds, port 8000; not
part of this repo). With "bonds_port": 8000 in /etc/modpi/monitor.json, this site serves it at
/bonds/ to signed-in users only (never when login is off), rewriting its root links to /bonds/,
never forwarding the session cookie, and marking every reply private, no-store so Cloudflare
can't cache bond data. A footer link appears next to Admin. Recommended: a systemd override so the
tracker listens on 127.0.0.1 only; otherwise anyone on the home network can open it without login.
