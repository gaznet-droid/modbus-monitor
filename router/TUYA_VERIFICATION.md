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

## Verified allowance and proposed rollout (not enabled)

The dedicated IoT Core service page showed Cloud Develop Base Resource Trial, monthly allowance US$0.20, in service from 23 October to 23 November 2026. Tuya's official pricing page (https://developer.tuya.com/en/docs/iot/membership-service?id=K9m8k45jwvg9j, updated July 2026) lists Trial API calls outside mainland China at US$3.71 per million, with approximately 26,000 calls when allocating half the allowance to APIs; the trial suspends at exhaustion instead of allowing paid overage.

Proposed cloud fallback: two device status reads per 900-second cycle, cached token reuse, about 216 requests/day in normal operation including token calls. Proposed local ceilings: 300 requests/day and 10,000/month, counting retries and tokens. The 10,000-request ceiling corresponds to about US$0.0371 of API resource usage under that listed rate. No message subscription is added. Account-wide usage and remaining allowance still require monitoring. No subscription, purchase, trial extension or automatic polling was enabled.

Gareth's deliberate cloud-fallback approval is pending, because the original programme prefers routine LAN collection and disallows silently substituting routine cloud polling. Trial expiry must remain a hard boundary for any approved initial run.

## Automatic cloud rollout — 23 October 2026

Gareth authorised temporary automatic Shed/Garage collection while a local collector is evaluated. Deployed a 900-second schedule with persistent 300/day and 10,000/calendar-month request caps (tokens and retries included). Requests stop before transport at the trial boundary: 23 November 2026 00:00 UTC, earlier than the displayed service expiry. Missing/expired deadline refuses startup. Budget/expiry stops do not respawn.

New /etc/init.d/modbus-tuya is enabled at boot, nice 19, with no respawn policy. Only this new collector was started; monitor, tunnels and network were not restarted. Existing USB guard and separate database remain in use. First automatic cycle consumed three requests, wrote both samples, and had no device errors. Health and latest returned HTTP 200. Available router RAM after startup was approximately 34 MB.

Private router backup: /etc/modbus-tuya-cloud-backup-20261023 (original collector plus private config). Roll back with /etc/init.d/modbus-tuya stop and disable, then restore the backed-up collector/config. Do not delete history or request accounting.

21 tests pass, including transport prevention at expiry, restart persistence, missing/invalid deadline rejection, existing budgets and USB loss. Browser clients continue reading bounded cached USB queries; no Worker/D1 writes or per-view Tuya calls were added. This collector cannot control usage by other applications sharing the Tuya allowance.

### Rollout blocked by router unresponsiveness

After the first successful automatic cycle and HTTP 200 latest verification, follow-up history requests stalled. Fresh SSH timed out during banner exchange, HTTPS management timed out, and the existing session stopped returning output. Therefore this rollout is NOT accepted as healthy. Cause is unconfirmed. On recovering access, first stop and disable modbus-tuya, preserve USB databases and accounting, then inspect router logs/storage/memory before re-enabling collection. The service was enabled before loss of access and has not yet been disabled.

### Recovery verification — 25 October 2026

Gareth restored SSH access. Router uptime was 1 day 22 hours; available RAM about 55 MB. Temporarily stopped/disabled modbus-tuya during inspection. USB had 191 genuine samples per device, latest 15:16 UTC on 25 October, and persistent accounting showed 143 requests today / 442 in October. Both 24-hour history endpoints returned HTTP 200 with 96 points; health also returned HTTP 200. Available kernel/service logs had no matching OOM/USB errors. This demonstrates collection continued after the subsequent reboot; it does not establish Friday's failure cause. Restored the previously authorised 900-second schedule with unchanged caps/deadline and persistent restart gate.

### Generation period UI — prepared 25 October 2026

Shared Generation container wraps Shed, Garage and Total. One period selector updates all kWh estimates and chart: Live (past hour), Today (UK midnight to now), rolling 24 hours, preceding 24 hours, Yesterday (UK calendar day), 7 days and 30 days. Each individual card retains latest W/V/A plus estimated kWh on its right. Total sums latest fresh power and the selected-period energy estimates; absent device energy prevents a misleading combined total. Removed routine collection-status sentence.

Energy is trapezoidal integration of retained minute power averages. Clips/interpolates window boundaries, never extrapolates beyond observations, never bridges gaps over 30 minutes, rejects nonfinite readings and integrates only the positive portion of signed power (reverse flow is zero generation, with coverage retained), reports per-device coverage. Sparse 15-minute samples make this an estimate, not a cumulative meter register. Existing raw retention is seven days; minute rollups supply older period energy. Queries are indexed, streamed and cached, chart remains bounded to 1000 points; collector, budgets, USB identity guard and cloud calls are unchanged.

25 tests pass including exact constant-power integration, clipped/ramped intervals, missing samples, retained rollup energy without raw samples, and 23/25-hour UK calendar days. Chrome mobile (390px) and desktop (1200px) checks pass: 3.750 kWh combined fixture, 19.39 W combined fresh fixture, all selector choices, lazy requests, no hidden polling, no extra requests on metric changes, voltage lifecycle, keyboard tabs and no JS errors. Screenshots inspected at both widths. JS syntax passes.

Deployment prepared with expected live SHA checks and private two-file backup; restart only modbus-monitor after applying, leaving collector/tunnels/network running. Roll back by restoring tuya_cached.py and web/index.html from /etc/modbus-generation-backup-TIMESTAMP and restarting modbus-monitor. NOT deployed yet: prior SSH session closed and fresh login requires user authentication.

### Generation deployment completed — 25 October 2026

Applied after expected live SHA checks. Original two-file backup: /etc/modbus-generation-backup-20261025-174031. Restarted only modbus-monitor. Real signed negative Shed readings led to refinement: integrate only positive portions of signed-power intervals, including zero crossings. Reverse flow retains coverage and contributes zero generation. Latest W and chart retain signed values. All 26 tests pass.

All seven new periods returned HTTP 200 for both devices, health passed, collector stayed running. Today: Shed 0.318970 kWh + Garage 0.764338 kWh = Total 1.083308 kWh with 18.52 hours covered. Yesterday total 0.211146 kWh. Week/month total 1.305834 kWh over 49.53 hours recorded. Public Chrome rendered three cards and changed period totals before the signed-power refinement; API verified afterwards. Final browser reread could not run because that tab was closed. Collector rate/caps/trial deadline, USB databases, credentials and both tunnels unchanged.

## Mobile generation charts — prepared 25 October 2026

Authorised mobile-only follow-up: <=600px displays stacked Shed/Garage generation bars for Today/Yesterday/rolling24h/prior24h (hour bins), 7d (UK day bins), 30d (UK Monday week bins) with an explicit daily-detail drill-down and back button. Live uses a single Total power line by default with Total/Shed/Garage/Compare buttons. Voltage/current remain simplified lines. Desktop remains the existing three-line chart and existing card layout. Mobile adds selected-period total and component summary above the chart, reduced card spacing, short dates, a readable selected-bin detail panel, accessible period-detail dropdown, 44px controls, and expandable calculation notes. Missing/partial bins are hatched; measured zero retains numerical zero and coverage. Tap any bin to select, including missing slots.

Backend derives small bin arrays during the existing indexed, streamed, cached minute-rollup energy query. Interpolated segments are clipped at bin boundaries; positive signed power, no extrapolation and the >30min gap rule are preserved. Bin sums equal headline energy; day boundaries respect UK 23/25-hour DST days. Month daily drill-down reuses the same fetched data. No collector/config/storage changes or extra Tuya calls.

28 Python tests pass (including bin sum/coverage conservation, missing versus zero versus partial readings, calendar boundary/DST coverage and signed zero crossings). JS syntax passes. Real Chrome QA passes at 390px/320px and desktop1200px: actual touch selection, all period choices, weekly drill/back without fetch, one-line live default, voltage/current lines, hidden-page polling suspension, lazy tabs, shared cached metric changes, keyboard navigation, no overflow/no JS errors. Mobile and desktop screenshots inspected.

Prepared /tmp/modbus-mobile-generation-deploy-command.txt with expected live source hashes, router-root guard and private two-file backup. NOT deployed yet: original SSH session closed; fresh login needs user authentication. After login apply then restart only modbus-monitor, verify health + all periods and energy/bin sums, collector running, and public UI. Rollback restores tuya_cached.py and web/index.html from /etc/modbus-mobile-generation-backup-TIMESTAMP and restarts only modbus-monitor.

### Mobile chart rollout confirmed / portrait cards prepared

Terminal output confirms mobile chart release 1ef885a deployed and all period API/bin-sum checks passed at 21:15:45 UTC 25 October. Backup /etc/modbus-mobile-generation-backup-20261025-211545. Collector remained running.

Authorised portrait-card refinement: mobile Shed/Garage now use two columns, generation-first vertical content, shorter labels, one shared estimate notice, no repeated period labels, compact V/A rows and per-device times. Total remains full width. Only <=600px CSS and a hidden-on-desktop notice are added. Chrome QA passes at 320/390/1200px, touch/chart interactions and no overflow; screenshot inspected. HTML-only auto-deployment prepared at /tmp/modbus-portrait-auto.py; private backup and source-hash guard, local served-HTML/health checks, no service restart. Awaiting password authentication.
