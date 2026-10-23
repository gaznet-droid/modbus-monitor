# Tuya energy telemetry — staging only

This is an independent **read-only** collector for the Modbus project. It does **not** modify `monitor.py`, the live site, Cloudflare, Clock Mill Hub, or the Modbus 3-second meter poller. No procd registration or automatic scheduling is included until verification/approval.

## Confirmed on 23 October 2026

- A separately created Central Europe Tuya cloud project, `Modbus Tuya Monitor`, has one Smart Life account linked and **9/9 devices** in the project's device list.
- The `GET /v1.0/devices/{id}/status` API is successful for **Juwai Shed** and **Juwai Garage**. Tuya reports `cur_voltage`, `cur_current`, and `cur_power`. Divide their integer readings by **100**, **1000**, and **100** respectively.
- The **A incoming V B solar** device is listed in Tuya's project but `GET /v1.0/devices/{id}/status` and `/v1.0/iot-03/devices/{id}/status` both return `1106 permission deny`. The v2.0 Thing API also returns `40001900 No space permission`. **Do not poll or represent this device as integrated until resolved.**
- Tuya's working Juwai status response has no battery percentage, capacity, resistance, temperature, or accumulated energy DPs. Do not make these up or infer them from current/power.
- The IoT Core and Smart Home Basic Service API authorisations were visible and in service. The current IoT Core trial lists an expiry of **23 November 2026**. Actual costs and future allowance still need monitoring.
- The Mac can reach TCP/22 on the RUTX12 and the existing public `/health` endpoint returned HTTP 200, but unattended SSH was denied. **Router USB state was not inspected or changed.**

## Private router setup (not yet deployed)

1. Put this script alongside `monitor.py` in `/usr/local/usr/lib/modbus-monitor/tuya_collector.py`.
2. Create `/etc/modbus-tuya.json` **on the router's internal private filesystem**, with mode `0600`. Do not put secrets in GitHub, HTML, the USB disk, telemetry, or logs.

Example **with placeholders only**:

```json
{
  "client_id": "REPLACE_ON_ROUTER",
  "client_secret": "REPLACE_ON_ROUTER",
  "interval_seconds": 900,
  "devices": {
    "shed": "REPLACE_WITH_SHED_DEVICE_ID",
    "garage": "REPLACE_WITH_GARAGE_DEVICE_ID"
  }
}
```

3. Run `/usr/local/usr/bin/python3 tuya_collector.py --config /etc/modbus-tuya.json --probe` to test **GET only**, without touching the USB.
4. Once validated, `--once` writes a sample to the dedicated USB through `modbus_usb.storage_directory()` and `modbus_usb.check()`. Database path on the USB: `modbus-data/tuya.sqlite3`, separate from `monitor.sqlite3`.
5. Routine Tuya **cloud polling is not the preferred default** in the original project brief; local reads are preferred only where the devices are actually reachable on the router LAN. A separate LAN or unsupported local protocol may require a cloud fallback. Only after verifying LAN topology, quota and obtaining a deliberate rollout decision should a 15-minute cloud schedule be enabled. This staging script does **not** create a schedule. No HTTP handler or browser feature is published by it.

The script permits only the Tuya Central Europe endpoint, only GET requests, and only `/v1.0/token?grant_type=1` and `/v1.0/devices/<id>/status`. It does not issue relays, commands or configuration changes. It also rejects cloud intervals shorter than 900 seconds to protect the low trial quota.

The SQLite file uses DELETE journal mode, FULL sync, one writer, raw samples retained seven days, minute rollups retained one year and hourly rollups retained indefinitely. No USB means no writes. The USB mount identity guard and directory FD are inherited from the current live Modbus service.

## Acceptance still outstanding

- Check whether Dad's LAN is actually reachable from the RUTX12; if not, do not attempt Tuya LAN connections across the internet. Verify the router can reach Tuya cloud and the dedicated USB remains mounted, using an authorised SSH session.
- Run a one-shot read-only probe on the router, then an isolated one-shot USB write.
- Decide how to expose guarded, read-only *cached* Juwai readings alongside the existing meter dashboard without introducing browser-side cloud calls.
- Solve the solar `1106` permission problem with Tuya or an account/region check; never solve it by widening to device controls.
- Establish whether the Juwai manufacturer offers safe, supported access to battery/temperature DPs; do not assume the public cloud API exposes them.
- Inspect quota consumption before choosing a higher polling frequency. Initial cadence is 15 minutes; no Worker/D1 needed.

Run unit tests from the repo root with `python3 -m unittest discover -s tests -v`.
