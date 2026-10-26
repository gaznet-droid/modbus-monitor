# Dad's Modbus eWeLink integration

Deployed 26 October 2026. Dedicated eWeLink tab for 1000 watt inverter, Garage inverter, 600 watt inverter, Charger inverter and Fridge. No device-control routes or commands exist in the adapter.

Status: page, cached endpoints and OAuth callback deployed and checked. No app secret, OAuth token, device mapping or genuine eWeLink samples have been installed yet. Secure cloud-browser sign-in could not be resumed; do not report the devices as connected.

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
