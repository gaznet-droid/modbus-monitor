# Modbus Tuya staging verification — 23 October 2026

Target: https://modbus.clockmillbarn.uk. No Clock Mill Hub source or services changed.

## Verified

- Source worktree: /Users/garethbrown/modbus-tuya-staging, branch tuya-readonly-staging, based on d76dc67. Existing router monitor.py is newer than that reference and was not replaced.
- Ten existing and added unit tests pass on the Mac.
- Installed only router/tuya_collector.py at /usr/local/usr/lib/modbus-monitor/tuya_collector.py; SHA256 4a39ec4604f3695fc20f521d8689fbfa74083a43263d6550095cb4c0214e39f6.
- Dedicated Modbus project credentials stored at /etc/modbus-tuya.json, root-only mode 0600. No secrets in this repository.
- Router --probe succeeded for Shed and Garage. Solar excluded because its API permission problem remains unresolved.
- One bounded --once call succeeded. Guarded USB tuya.sqlite3 contains exactly one sample per device and four rollup rows. PRAGMA quick_check returned ok.
- Local Modbus health remained healthy after both calls. Public health returned HTTP 200 after tunnel reconnection following reboot.
- No Tuya init service or cron schedule has been enabled. Cached API and Juwai UI are prepared for deployment after 19 unit tests and Chrome mobile/desktop checks passed.

## Recovery incident

Router SSH and HTTPS stopped responding during staging. The collector itself had not been started and no Tuya credentials had been sent to the router at that point. User power-cycled the supplying circuit and reported recovery. Post-reboot resource check showed about 44 MiB available RAM on a 240 MiB device, five existing Python processes, two cloudflared processes and tailscaled. No matching OOM or I/O errors were present in the current log; this does not establish the pre-reboot cause. RMS was attempting connections but rejected for lack of active credit; no credits purchased or activated. Do not infer a confirmed circuit mapping from this incident.

## Remaining gates

- Validate readings with Dad's app/display.
- Verify Dad's LAN topology and whether supported local access exists. Cloud fallback is a deliberate decision; no automatic cloud polling has been approved here.
- Review quota and add scheduling locks, backoff and storage-failure recovery before enabling any continuous cloud service.
- Expose bounded read-only cached data through the existing Modbus origin and add UI only after the collection route is agreed.

## Cached-view validation

- Nineteen tests pass on Linux and Mac, including bounded/indexed cached reads, shared-client caching, storage-loss/negative-cache recovery, read-only HTTP behavior, persistent quota/cycle gates and exclusive locking.
- Chrome passes lazy provider requests, cached metric selection, hidden-page pause, lazy voltage start/stop, keyboard navigation, 390px mobile layout and desktop layout with no JS errors.
- Dad confirmed the observed meter readings via Gareth before UI rollout.
- Automatic cloud polling is prepared but disabled; no claim of LAN access, quota approval or stable continuous collection is made.

## Deployed cached view

Deployed code revision 81718f2 on 23 October 2026. Baseline hashes matched the router before installation. A root-private source backup is at /etc/modbus-tuya-backup-81718f2. The existing Modbus service alone was restarted after read-only cached queries passed on the router; no other service was restarted. No database or credential contents were included in the source payload.

Public health, latest Juwai, both Juwai history routes and existing 8-hour/calendar-day/24-hour history routes returned HTTP 200. Unsupported solar queries returned 400. A production Chrome check confirmed current HTML and real USB samples, and no Voltage calls while viewing Power/Juwai. Automatic Tuya collection remains disabled.

The stored test samples at this point are Shed 26.47 V / 0.038 A / 1.02 W and Garage 21.13 V / 0.869 A / 18.37 W, observed at 2026-10-23 12:49:57–58 UTC. Their age is always shown; these are not labelled as continuously live.
