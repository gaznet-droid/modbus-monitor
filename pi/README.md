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
5. Checks the app answers /version on port 8096 within a minute (not /, which redirects to sign-in when login is on). If not, it restores the
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
can't cache bond data. A footer link appears next to Admin. Since 8 Nov the override
/etc/systemd/system/bondtracker.service.d/local-only.conf makes the tracker listen on 127.0.0.1 only;
Oscar reaches it through the site or an SSH tunnel (ssh -L 8000:127.0.0.1:8000 admin@modpi).

## Shutting down from the Admin page
Admin has a "Shut down the Pi" button (two taps). The site, running as the unprivileged modpi
user, only writes /var/lib/modpi/shutdown-request; modpi-shutdown.path (root) sees it and
modpi-shutdown.service powers off cleanly. Only a request less than two minutes old counts, and
the file is always deleted first, so a leftover request can never shut the Pi down after a boot.
Only signed-in users on the site's own origin can ask; with login off there is no button. Wait for
the green light to stop flashing before unplugging. Installed with pi/install-autodeploy.sh.

## Local Juwai reads (TEST, from 11 Nov 2026)
A separate test collector, modpi-juwai-local.service, reads the Shed and Garage Juwai meters
straight over Dad's LAN every 60 seconds (Tuya local protocol 3.5, read-only status queries
only; code in router/juwai_local.py, standard library plus python3-cryptography). It writes to
its own database, /var/lib/modpi/data/juwai_local.sqlite3. The cloud collector (modpi-tuya), its
15-minute schedule, request caps, 23 November stop and tuya.sqlite3 are untouched.

- Private settings: /var/lib/modpi/private/juwai-local.json (modpi:modpi, 0600) holds each
  meter's LAN address and protocol version, the LAN range to search if a meter moves, and
  `interval_seconds` (60; change it and restart the test unit). Keys stay in tuya-local-keys.json.
  None of this is ever committed.
- If a meter stops answering five times in a row the collector looks (then again at most every
  5 minutes while it is still missing) for it at a new address, proves it with the key, saves the new address privately and records an
  `address_changed` event (no addresses in the database or the journal).
- Also in that database, for the test report only: `read_stats` (reads that worked and failed per
  day), `events`, and `compare` (each cloud sample paired with the local sample nearest in time).
- One supervised read, nothing stored (stop the unit first; they share a lock):
  `sudo -u modpi python3 /opt/modpi/router/juwai_local.py --probe`
- One-off, before switching the page to local: copy the cloud history from before the local
  history starts (router/juwai_history_import.py; cloud database opened read-only, safe to run
  twice, refuses while the collector holds its lock). Stop the unit, dry-run, apply, start it:
  `sudo systemctl stop modpi-juwai-local`, then `sudo -u modpi python3 /opt/modpi/router/juwai_history_import.py
  --dry-run|--apply --cloud-db <data dir>/tuya.sqlite3 --local-db <data dir>/juwai_local.sqlite3
  --lock-file /var/lib/modpi/private/juwai-local.lock`, then `sudo systemctl start modpi-juwai-local`.
- Switch the test off: `sudo systemctl disable --now modpi-juwai-local`. Nothing else depends on it.
- The unit is installed by hand (copy pi/modpi-juwai-local.service to /etc/systemd/system, then
  `sudo systemctl daemon-reload && sudo systemctl enable --now modpi-juwai-local`). Auto-deploy
  updates its code with the rest of router/ but does not restart it; restart it by hand after a
  change to juwai_local.py.

The Juwai page reads one source, set by `"juwai_source": "cloud"` (the default) or `"local"` in
/etc/modpi/monitor.json, followed by a modpi restart. While local reads are on test, signed-in
users get a Cloud / Local switch at the top of the page; each card shows its source and the time
of its reading, and local readings older than five minutes are marked Delayed. Since 12 Nov 2026 the
Pi is set to `"local"`; the cloud collector runs on as the fallback until 23 November.

## Local eWeLink reads for the power plugs (from 13 Nov 2026)
router/ewelink_local.py runs inside modpi when `"ewelink_local_enabled": true` is set in
/etc/modpi/monitor.json (then restart modpi). It listens for the plugs' mDNS announcements
(_ewelink._tcp), which carry their state encrypted with each plug's device key, and every 10
seconds asks, by a standard mDNS question, for them to repeat it. Read-only: no command or
setting is ever sent to a plug, and nothing in the eWeLink account changes.

- Keys: /var/lib/modpi/private/ewelink-local.json (modpi:modpi, 0600) holds each plug's device
  ID, key and UIID, taken once from the cloud device list. Never commit, print or log it.
  Re-pairing a plug changes its key; fetch it again with one device-list call.
- Only UIID 190 plugs (1000 watt inverter, Fridge) are read locally: they announce every change.
  UIID 32 plugs (Garage, 600 watt, Charger) only repeat the state they last pushed, sometimes hours
  old, and live power from them would need eWeLink's uiActive write command, so they stay on the
  cloud poll.
- Readings go into the same ewelink.sqlite3: the latest reading at most every 5 seconds, and a
  history sample every 30 seconds (time-weighted average power). Each reading is marked
  `"source": "local"` or `"cloud"`. History is averaged per five-minute slot before it is
  served, so the pages' energy sums are unchanged.
- A plug not heard for 60 seconds drops back to the 5-minute cloud poll; the cloud poll skips
  plugs heard locally in the last minute. Changes are logged as `ewelink-local: <slug>
  local_lost|local_back` in the modpi journal and in the `local_events` table (kept 90 days).
- Switch off: set `"ewelink_local_enabled": false` and restart modpi. The cloud poll carries on.

## Meter over the RS485 HAT (Job 3, live since 11 Nov 2026)
router/modbus_rtu.py reads the meter over the Pi's serial port (Modbus RTU, 9600 8N1, unit 1,
read functions 03 and 04 only), using the standard library. The same three reads as over the
Elfin: power (register 146), voltage (register 16) and the combined kWh (register 1792).

The HAT is a Waveshare RS485 CAN HAT Rev2.1. It does NOT switch between sending and receiving by
itself: GPIO4 high sends, low listens, and the HAT pulls it high (send) when nothing drives it.
The reader drives GPIO4 through the kernel GPIO device, holding it high only for the request.
Proven on site on 11 Nov: 60 reads out of 60 with the Elfin still on the same pair.

On the Pi: `enable_uart=1`, `dtoverlay=disable-bt` and `gpio=4=op,dl` (HAT listening from power-on)
in /boot/firmware/config.txt; no serial console or login on the port; the modpi user is in the
dialout and gpio groups. Settings in /etc/modpi/monitor.json:

    "meter_serial": "/dev/serial0", "meter_direction_gpio": 4      (optional "meter_baud", default 9600)

While meter_serial is set, meter_host is ignored. Wiring: the meter's RS485 pair on the HAT's A and
B terminals (not the CAN H and L pair).

Supervised read (stop modpi first; they cannot share the port):
`sudo -u modpi python3 /opt/modpi/router/modbus_rtu.py /dev/serial0 9600 4`

Roll back to the Elfin: remove meter_serial from monitor.json and restart modpi; the Elfin must be
wired to the meter and reachable at meter_host.

## Epever solar charger (listen-only, from 12 Nov 2026)
Dad's EPEVER Tracer-AN charger has a Wi-Fi dongle (Hi-Flying HF-LPT270, HuiNeng firmware) that feeds
the Solar Guardian app over its own link to EPEVER's server. That link is untouched. The dongle's
unused second connection ("socket B") is pointed at the Pi, and the dongle copies the charger's Modbus
replies to it. router/epever.py (modpi-epever.service) accepts that connection, reads, and stores; it
has no code that sends, so it cannot switch the load or change a setting. Registers and scaling follow
EPEVER's Controller Communication Instruction V2.5. Only replies arrive, not requests, so each reply
is recognised by its length and place in the burst (see the top of router/epever.py).

- What arrives: battery voltage and charging state about once a minute; panel power, battery current,
  load, temperatures, state of charge and the energy counters every few minutes, and more often while
  someone has the app open.
- Data: /var/lib/modpi/data/epever.sqlite3. Generation for a period is the difference in the charger's
  own lifetime counter, not an estimate from power.
- Private settings: /var/lib/modpi/private/epever.json (modpi:modpi, 0600): `{"port": 15001, "name": "Epever"}`.
  The name is the card's title. The saved dongle settings pages, setup-command output and the first
  raw capture are in /var/lib/modpi/private/epever/. Never commit any of it.
- Site: a fourth card on the Generation page, shown only once readings exist; tap it for the battery
  detail and graph. It is NOT added to the Shed and garage total (Dad to confirm whether its panels overlap).
- The unit is installed by hand, like modpi-juwai-local: copy pi/modpi-epever.service to
  /etc/systemd/system, then `sudo systemctl daemon-reload && sudo systemctl enable --now modpi-epever`.
  Auto-deploy updates its code but does not restart it; restart it by hand after a change to epever.py.
  It is separate from modpi so a site deploy does not drop the dongle's connection.
- The dongle's setup commands are Hi-Flying AT commands over UDP port 48899 on the LAN (send the
  discovery text, then `+ok`, then commands, then `AT+Q`). Socket B was set with
  `AT+SOCKB=TCP,<port>,<the Pi's LAN address>` then `AT+Z` to restart; the dongle takes about five
  minutes to rejoin. Undo: `AT+SOCKB=NONE` then `AT+Z`. The Pi needs a fixed address on Dad's router
  (reserved 12 Nov). The dongle's own address can change; it calls the Pi.
- Do not fill in the MQTT fields on the dongle's Account page. They look blank but hold the Solar
  Guardian settings; saving over them cuts the app off.
- pi/epever_tap.py is the raw logger and relay used for the first test; keep it for fault-finding
  (stop modpi-epever first; they use the same port).
