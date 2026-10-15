# Teltonika Modbus monitor

An independent, read-only dashboard and collector for Dad's power meter. The existing
Render `app.py` is preserved for rollback. The router service uses only Python 3's
standard library; it does not install FastAPI, Uvicorn or PyModbus on the router.

## Goal and current state — 15 October 2026

1. Router capacity and meter connectivity checked.
2. Continuous three-second collection installed and enabled on RUTX12. SQLite data
   lives on the dedicated 16 GB SanDisk USB. Missing-drive guards and recovery tested.
3. Dashboard implemented with incremental updates, stale-reading status and history
   ranges: 15/30 minutes, 1/2/4 hours, 1 day, 1 week, 30 days, 90 days and 365 days.
4. Public publication is pending access to the Cloudflare account holding
   `clockmillbarn.uk`. The ONLY intended hostname is `modbus.clockmillbarn.uk`.
   It must be public without an Access login, password or VPN requirement.
5. Render must remain available until the public replacement is verified, then be
   suspended (not deleted) to stop duplicate polling and preserve rollback.

The connected Cloudflare API sees Gareth's account and the `clockmill-router`
tunnel, but no `clockmillbarn.uk` zone. Tunnel and DNS zone must belong to the same
account. Do not point Oscar's zone at Gareth's tunnel; create/use a tunnel in the
zone's account after access is available. Do not change the existing tunnel: it
supports unrelated Clock Mill management access.

## Installed paths

- `/usr/local/usr/lib/modbus-monitor/monitor.py`
- `/usr/local/usr/lib/modbus-monitor/web/index.html`
- `/usr/local/usr/lib/modbus-monitor/web/chart.umd.min.js` (Chart.js 4.5.1, MIT)
- `/etc/modbus-monitor.json` (meter host/port and loopback bind/port; mode 0600)
- `/etc/init.d/modbus-monitor` (procd respawn, boot priority 96)
- `/usr/local/usr/lib/clockmill/modbus_usb.py` (USB identity and storage guard)
- `/etc/init.d/modbus-usb` (boot priority 95)
- `/etc/hotplug.d/block/95-modbus-usb`
- `/usr/local/mnt/modbus-usb/modbus-data/monitor.sqlite3` (database on USB)

Service binds `127.0.0.1:8096`; publish only this HTTP origin through a named
Cloudflare Tunnel in the domain's account. No Worker is needed. Do not publish SSH,
the router admin interface, database files or configuration files.

Example `/etc/modbus-monitor.json`:

```json
{"meter_host":"2.98.153.184","meter_port":8838,"bind":"127.0.0.1","port":8096}
```

`modbus_usb.py` currently identifies the installed SanDisk by its USB serial; adapt
that constant deliberately when replacing the USB. It must never silently select
an unrelated drive. exFAT works with the router's kernel driver even though its
`block info` utility does not identify this volume. Custom boot/hotplug mounting
therefore avoids changing global fstab behavior or other USB devices.

## Data behavior

- Reads input registers 146–147, unit 1, function 04, big-endian float32. No meter
  writes or control endpoints are implemented.
- Valid readings get UTC Unix timestamps in milliseconds. The browser displays
  local dates and times.
- Raw samples retained for approximately seven days, minute rollups for one year,
  hourly rollups indefinitely. Cleanup is batched hourly. Long-term charts are
  bounded to approximately 1,000 average points, with min/max values on hover.
- Import/export Wh are integrated between valid samples, splitting zero crossings.
  Intervals longer than ten seconds are omitted rather than estimated. Totals are
  cumulative measured intervals, not billing figures or reconstructed past usage.
- The UI marks readings stale after twelve seconds without a fresh sample.
- SQLite uses rollback journals, FULL synchronization and a small memory cache.
  The USB directory is pinned for the database connection lifetime. Every operation
  rechecks USB presence and mount state. USB removal stops the dedicated service;
  reinsertion mounts the intended device and starts it again.
- A process restart preserves history and totals. Internet/meter failures are
  retried without fabricating readings. Local collection cannot recover readings
  from times when either site's internet was unavailable.
- No long-term Render archive exists to backfill: history builds from installation.

## Validation and operation

```sh
python3 -m unittest discover -s tests -v
node --check router/web/monitor.js
```

On the router:

```sh
/etc/init.d/modbus-monitor status
/usr/local/usr/bin/python3 /usr/local/usr/lib/clockmill/modbus_usb.py check
curl -fsS http://127.0.0.1:8096/health
```

`/health` returns 503 if readings are stale or USB storage is unavailable.
`/api/live?since=<timestamp>` returns new raw samples plus current status/totals.
`/api/history?window=30d` returns the selected aggregated history. All are read-only.
Unsupported paths are 404; no generic file server is exposed. Concurrent HTTP
requests are bounded to twelve to limit router RAM use. Chart.js is served locally.

Tested: real meter read, continuous collection, USB create/write/read/delete,
simulated remove/add hooks, refusal of unmounted storage with no internal fallback
directory, persistence across service restart, history range endpoints, browser
JavaScript and mobile layout. A physical router reboot and actual unplug/replug
remain untested to avoid interrupting household networking.

Rollback: stop/disable only `modbus-monitor`; keep the USB/database and Render
service. A backup of the earlier USB hotplug hook is at
`/etc/modbus-monitor-backup/95-modbus-usb`. Do not alter existing Clock Mill services.

`web/index.html` embeds `web/monitor.js`; regenerate that embedded script after
editing it before copying the HTML to the router.
