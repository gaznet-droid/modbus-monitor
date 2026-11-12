#!/usr/bin/env python3
"""One-off: copy cloud Juwai history from before the local history starts into the local database.

For each meter, cloud history older than that meter's first local data is copied from the cloud
database (opened strictly read-only, never changed) into the local one: the minute and hourly
rollups the Juwai page charts from, plus whatever raw samples the cloud still holds. Cloud data at
or after the first local data is never copied. Run it with the local collector stopped: applying
holds the collector's lock, so it refuses to run while the collector is up. Safe to run twice:
each meter is marked once copied and is then skipped. Standard library only; nothing here
contacts a meter or Tuya.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tuya_cached import DEVICES  # noqa: E402
from tuya_collector import process_lock  # noqa: E402

COLUMNS = "device,ts,voltage_v,current_a,power_w,raw_json"
ROLLUP_COLUMNS = "bucket,n,voltage_sum,voltage_n,current_sum,current_n,power_sum,power_n"
# Adds whole rollup rows the way USBStore.add adds one sample, so imported rollups match collected ones.
# (USBStore.add is not used: it also prunes samples older than a week, which would delete the history.)
ROLLUP = """INSERT INTO rollups
    (device,period,bucket,n,voltage_sum,voltage_n,current_sum,current_n,power_sum,power_n)
    VALUES (?,?,?,?,?,?,?,?,?,?)
    ON CONFLICT(device,period,bucket) DO UPDATE SET
    n=n+excluded.n, voltage_sum=voltage_sum+excluded.voltage_sum, voltage_n=voltage_n+excluded.voltage_n,
    current_sum=current_sum+excluded.current_sum, current_n=current_n+excluded.current_n,
    power_sum=power_sum+excluded.power_sum, power_n=power_n+excluded.power_n"""


def open_readonly(path):
    return sqlite3.connect("file:" + quote(str(Path(path).resolve())) + "?mode=ro", uri=True, timeout=5)


def first_local(local, device):
    """Start of the local history: first minute rollup (outlives pruned raw samples), else first sample."""
    row = local.execute("SELECT MIN(bucket) FROM rollups WHERE device=? AND period=60", (device,)).fetchone()
    if row[0] is None:
        row = local.execute("SELECT MIN(ts) FROM samples WHERE device=?", (device,)).fetchone()
    return row[0]


def marker_name(device):
    return "history_import_" + device


def plan(cloud, local):
    """Per meter: what would be copied. Reads only."""
    result = {}
    for device in DEVICES:
        done = local.execute("SELECT value FROM metadata WHERE name=?", (marker_name(device),)).fetchone()
        cutoff = first_local(local, device)
        info = {"cutoff": cutoff, "already_imported": bool(done), "samples": 0, "buckets": 0,
                "first": None, "last": None}
        if not done and cutoff is not None:
            info["samples"] = cloud.execute("SELECT COUNT(*) FROM samples WHERE device=? AND ts<?",
                                            (device, cutoff)).fetchone()[0]
            info["buckets"], info["first"], info["last"] = cloud.execute(
                "SELECT COUNT(*),MIN(bucket),MAX(bucket) FROM rollups WHERE device=? AND period=60 AND bucket<?",
                (device, cutoff)).fetchone()
        result[device] = info
    return result


def apply_import(cloud, local, now=None):
    """Copy, one transaction per meter. Returns (raw samples, minute buckets) copied per meter.

    The cloud keeps raw samples only a week, so older history exists only as rollups: the minute
    rollups before the join are copied, and the hourly ones are built from those same minutes
    (as USBStore.add builds them), so the two stay consistent even for the hour holding the join.
    """
    copied = {}
    for device, info in plan(cloud, local).items():
        copied[device] = (0, 0)
        if info["already_imported"] or info["cutoff"] is None:
            continue
        samples = cloud.execute("SELECT " + COLUMNS + " FROM samples WHERE device=? AND ts<? ORDER BY ts",
                                (device, info["cutoff"])).fetchall()
        minutes = cloud.execute("SELECT " + ROLLUP_COLUMNS + " FROM rollups WHERE device=? AND period=60 "
                                "AND bucket<? ORDER BY bucket", (device, info["cutoff"])).fetchall()
        with local:  # one transaction: all of a meter's history and its marker, or none
            local.executemany("INSERT OR IGNORE INTO samples (" + COLUMNS + ") VALUES (?,?,?,?,?,?)", samples)
            for row in minutes:
                local.execute(ROLLUP, (device, 60, *row))
                local.execute(ROLLUP, (device, 3600, row[0] // 3600000 * 3600000, *row[1:]))
            local.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", (marker_name(device), json.dumps(
                {"cutoff": info["cutoff"], "samples": len(samples), "buckets": len(minutes),
                 "at": int((time.time() if now is None else now) * 1000)})))
        copied[device] = (len(samples), len(minutes))
    return copied


def stamp(ms):
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(ms / 1000)) + " UTC" if ms is not None else "-"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--cloud-db", required=True, help="tuya.sqlite3 (opened read-only)")
    parser.add_argument("--local-db", required=True, help="juwai_local.sqlite3 (must already exist)")
    parser.add_argument("--lock-file", required=True, help="the local collector's lock file")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Report what would be copied; change nothing")
    mode.add_argument("--apply", action="store_true", help="Copy the history (local collector must be stopped)")
    args = parser.parse_args(argv)
    for path in (args.cloud_db, args.local_db):
        if not os.path.isfile(path):
            parser.error("no such database: " + path)
    cloud = open_readonly(args.cloud_db)
    cloud.execute("PRAGMA query_only=ON")
    try:
        if args.dry_run:
            local = open_readonly(args.local_db)
            report = plan(cloud, local)
            local.close()
        else:
            try:
                with process_lock(args.lock_file):  # the collector holds this while it runs
                    local = sqlite3.connect(args.local_db, timeout=5)
                    local.execute("PRAGMA synchronous=FULL")
                    try:
                        report = plan(cloud, local)
                        copied = apply_import(cloud, local)
                    finally:
                        local.close()
            except RuntimeError as exc:
                print("Not run: " + str(exc) + ". Stop the local collector first.", file=sys.stderr)
                return 2
            for device in report:
                report[device]["samples"], report[device]["buckets"] = copied[device]
    finally:
        cloud.close()
    for device, info in report.items():
        if info["already_imported"]:
            print(device + ": already imported, skipped")
        elif info["cutoff"] is None:
            print(device + ": no local history yet, skipped")
        else:
            print("%s %s: %d minute rollups (%s to %s) and %d raw samples, all before local history at %s" % (
                "would copy" if args.dry_run else "copied", device, info["buckets"],
                stamp(info["first"]), stamp(info["last"]), info["samples"], stamp(info["cutoff"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
