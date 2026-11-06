# Dad's Modbus eWeLink integration

Deployed 26 October 2026. Dedicated eWeLink tab for 1000 watt inverter, Garage inverter, 600 watt inverter, Charger inverter and Fridge. No device-control routes or commands exist in the adapter.

Status: activated and verified 26 October 2026 at 14:52 UTC. App Secret and valid EU OAuth tokens are installed privately. All five requested devices are explicitly mapped, online and returning genuine stored measurements. Public latest and five history endpoints verified. Modbus health remains good. Cloud polling is active on the existing 300-second schedule.

Run on Gareth's Mac in Terminal:

```sh
python3 /Users/garethbrown/modbus-ewelink/router/setup_ewelink.py
```

The setup asks for the existing Hubless Hub for Dad App Secret using a hidden prompt. It sends the value in SSH stdin, not process arguments, and stores it in root-only /etc/modbus-ewelink.json. It opens the official OAuth page in the user's browser. Sign in to the account owning Dad's devices and review its consent. Once redirected to the hub, return to Terminal and select the matching device for each card. The adapter never guesses device identity from a similar name. If fewer than five supported devices are available, setup stops for review.

The developer app's Standard Role includes control permissions at eWeLink. The local adapter restricts transport to OAuth/token refresh and read APIs only. The user's consent must be reviewed accordingly; this is not a provider-enforced read-only scope.

OAuth: local root CLI creates a ten-minute one-use hashed state; no public connect/admin initiation route. Public callback validates state, expiry, uniqueness and fixed region, exchanges server-side and redirects to /#ewelink without exposing codes in response bodies. No request URL logging. Fixed HTTPS regional hosts, redirects refused, bounded responses. Cross-process flock serialises callback, discovery, collection and token rotation. Token/config files are 0600 on router internal storage, never on exFAT or the web root.

Collection: one batch of up to five explicitly selected devices every 300 seconds inside the existing Modbus process. Persistent attempt counters cap requests at 400/day and 12000/UTC calendar month, including errors and tokens. Persistent cycle gate prevents restart bursts. One-second call spacing, backoff for vendor failures/limits, no network collection before credentials and mapping exist, no vendor calls from public cached endpoints. Application expiry 26 October 2027 is enforced before transport. Refresh uses the OAuth chapter's /v2/user/refresh contract and atomically stores rotated tokens.

Storage: existing dedicated USB identity/mount guard, pinned directory fd, separate ewelink.sqlite3; rollback journal, FULL sync, bounded 30-day samples. Public latest/history reads use SQLite read-only mode, return only fixed public labels and selected measurement fields, omit vendor IDs/keys/raw payloads. Offline samples are excluded from power history. No interpolated kWh or sums mixing loads and inverters. Only scalar power/voltage/current fields with explicit engineering units are accepted; raw device-specific scaled fields remain unsupported until verified against the real UIID. Missing values stay unavailable.

UI: cached local latest request every 30 seconds only while eWeLink is selected/visible, history cached for 5 minutes; abort/version guards on hidden/tab changes; keyboard tabs; chart gaps for missing readings. Five card grid uses two columns including mobile. Existing Power, Voltage and Juwai behavior retained.

Validation: 48 Mac unit/HTTP tests pass; 17 new adapter tests also pass on Linux with /proc fd storage. Python compile and both inline/external JS syntax pass. Live browser AX confirmed all five cards and correct disconnected state. A secure-browser native-credential failure subsequently blocked screenshot/mobile inspection, so no mobile visual QA claim is made. Server deployment health and five-device latest verified. Full post-consent mapping, UIID/unit verification, live samples, history and refresh with the actual account remain pending.

Production source backup: /etc/modbus-ewelink-backup-20261026-142338. The later cross-process locking update backs up ewelink.py under this directory. Rollback restores monitor.py and web/index.html from backup, removes only the two newly added source files ewelink.py and web/ewelink.js, and restarts only modbus-monitor. Preserve private credentials/tokens/budget/schedule files and USB history. Do not restart networking, tunnels or the Tuya collector.

## Activation verification — 26 October 2026

Account consent had succeeded but device selection was empty. Matched cards to 1000W Inverter, Garage inverter 100w, 600w inverter, Charger, and Fridge Freezer. UIID32 reports engineering-unit decimal strings. UIID190 reports power/voltage/current in hundredths, verified against SonoffLAN devices.py (XSensor100 = multiply 0.01). Added UIID-aware conversion and a regression test before storing any samples. Forty-nine tests pass; eighteen adapter tests also pass on Linux. No control commands sent.

At first collection, all five were online: 1000 W inverter 3.35 W, garage 0 W/on, 600 W 0 W/off, charger 80.08 W, fridge 51.14 W. These are observations, not fixed values. History starts from activation; no older history is fabricated. Private activation backup: /etc/modbus-ewelink-activation-20261026-145223. Credentials, device IDs and raw vendor payloads are excluded from the repository.

## Responsive layout — 26 October 2026

Moved eWeLink inside the shared container (the detached section caused intrinsic-width shrinkage). Only the selected eWeLink view expands the container up to 1440px. Five equal cards at >=1100px, two columns on intermediate widths, compact single-column device rows at <=600px. Styling follows Juwai's group/cards and information order. The full-width graph shares the card grid's width; Juwai retains its existing 900px container.

Shared period control now offers Live/past hour, Today, rolling/previous 24h, Yesterday, 7d and 30d. Desktop always retains lines; phone Live is a line, other periods use average-power bars (W): hourly for day ranges, UK calendar days for 7d, Monday-aligned UK weeks for 30d. Monthly bars drill into stored daily detail and back without fetching. These are mean recorded watts, not estimated kWh. Missing slots remain null/hatched; zero readings remain zero. Details include sample counts and incomplete-data indicators.

History still reads the same USB samples and never contacts eWeLink; aggregation uses every valid online raw sample, rather than averaging pre-averaged chart groups. A single cached period per device bounds memory, expires after 60 seconds, and is invalidated on collection. No collector cadence, tokens, permissions, control endpoints or other providers changed.

Validated: 51 Python/HTTP tests; JS syntax; pure-JS responsive harness covers desktop lines, mobile hourly/daily/weekly bars, null vs zero, bar selection, drill/back without fetch, resize without fetch, hidden-page pause and cached-only URLs. Real desktop browser confirmed five equal columns, 1234px graph, no horizontal overflow, actual readings, device/range changes and Juwai's preserved 900px width. Mobile visual preview was unavailable in the browser environment; do not claim a phone screenshot was inspected.

Deployment backup: /etc/modbus-ewelink-responsive-20261026-154848 (ewelink.py, web/index.html, web/ewelink.js). Restore these three source files and restart only modbus-monitor for rollback; preserve all credentials and history. All seven period API responses plus latest and health verified on the router.

## Device icons and consolidated Power view — 26 October 2026

Eight inline SVG title icons cover five eWeLink devices plus Shed, Garage and their total. Latest power follows each title; connection/freshness status and a subtle pulsing dot sit beside the checked timestamp. Pulse stops for stale/unavailable samples and under prefers-reduced-motion. Juwai uses Current/Delayed to describe cached-reading freshness rather than claiming a network probe. Both card families put power first on mobile. Total title is Shed and garage total; the repeated Shed + Garage footer is removed.

Removed the Voltage tab. Its existing graph is a dedicated section beneath the Power donut. IntersectionObserver requires at least 5% of that section in view, with Power selected, document visible and page lifecycle active. Exit/hidden/tab/pagehide aborts and invalidates in-flight reads and timers. No background voltage storage or collector changes. Removed the individual-readings/local-history explanatory paragraph and its updater.

Checks: HTML structure/unique IDs/eight icons, inline JS syntax, existing responsive-chart harness, new pure-JS voltage visibility/version/lifecycle harness. Real browser shows all five icon cards and footer statuses, three generation icons/renamed total with genuine values, no overflow, removed Voltage navigation and history note. Scrolling to the new section obtained a genuine 234.96 V reading; moving offscreen showed Paused and version guards stop polling. Browser metadata-extension errors are unrelated.

Published atomically as an HTML-only change with expected prior hash and local served-body equality plus healthy meter status. No service restart or credential/history changes. Private source backup is recorded in the deployment output under /etc/modbus-ui-polish-TIMESTAMP.html. Restore that HTML for rollback; no restart is required.
