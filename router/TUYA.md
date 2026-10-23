# Modbus Juwai read-only integration

Target: https://modbus.clockmillbarn.uk. Separate project and credentials.

## Deployment and data flow

The router collector uses GET-only Tuya Central Europe APIs for the verified Shed and Garage devices. Values are stored on the dedicated guarded USB in modbus-data/tuya.sqlite3. The existing Modbus origin serves /api/tuya/latest and /api/tuya/history?device=shed|garage&window=24h|7d|30d|365d. These routes never contact a vendor API.

The Juwai dashboard tab shows separate stored V/A/W readings and per-device sample timestamps. Missing values remain unavailable, older values are marked stale, and charts leave gaps. No battery percentage, capacity, temperature, resistance or energy total is inferred. The solar meter remains disconnected because its Tuya permission issue is unresolved.

Latest data is cached for 30 seconds in the existing web process; identical history queries are cached for 300 seconds and bounded to 1000 plotted points. The cache has at most nine keys, one shared query lock and short negative caching during storage faults. Browser reads begin only when Juwai is visible and stop when hidden or switched away. Existing lazy Voltage behavior is preserved. No new Worker, D1 dependency, cloud deployment or background process is added.

## Private configuration

/etc/modbus-tuya.json belongs on the internal private filesystem, mode 0600. It contains client_id, client_secret, interval_seconds (minimum 900) and verified device aliases/IDs. No secrets belong in Git, USB, HTML or logs.

Explicit supervised commands:

    /usr/local/usr/bin/python3 /usr/local/usr/lib/modbus-monitor/tuya_collector.py --probe
    /usr/local/usr/bin/python3 /usr/local/usr/lib/modbus-monitor/tuya_collector.py --once

--probe does not write USB. --once writes one sample per available device. Both use an exclusive process lock. No mode is implicit. A failed/partial collection returns nonzero.

## Automatic cloud collection remains disabled

The project brief prefers supported local collection on Dad's LAN. LAN reachability and protocol access have not been established. Automatic cloud fallback requires a deliberate decision and verified allowance before enabling it.

The prepared --run mode additionally requires approved_cloud_polling=true, daily_request_budget and monthly_request_budget in the private config. Every actual request, including token activity and retries, reserves a USB-persisted UTC budget before sending. A persisted 900-second minimum cycle gate prevents restart bursts. Device failures receive independent exponential backoff, up to six hours. Authentication, exhausted budgets and HTTP rate-limit failures stop automatic polling for operator review. USB failures stop writes and requests; there is no internal-flash data fallback. No automatic procd service or cron entry has been installed. Do not run --run until quota is checked and the collection route is agreed.

Retention remains raw samples seven days, minute rollups one year and hourly rollups indefinitely, using DELETE journals and FULL synchronization. The existing Modbus history/database is separate.

## Validation

    python3 -m unittest discover -s tests -v
    node --check router/web/monitor.js

Chrome browser validation uses tests/preview_juwai.py and tests/check_juwai_browser.cjs, with a disposable headless Chrome profile and localhost debugging on port 9225. Preview data is fixtures only. Do not publish the preview server. router/web/monitor.js is the maintained copy of the script embedded in router/web/index.html; keep them identical.

The live timezone file is retained on the router. The source includes the Europe/London timezone data needed by the existing calendar-day view.

Rollback restores the backed-up monitor.py, web/index.html and tuya_collector.py, removes only the newly added tuya_cached.py, and restarts only modbus-monitor. Preserve both USB databases and private configuration.
