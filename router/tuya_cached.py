"""Bounded read-only USB queries. This module never contacts Tuya or devices."""
import copy
from bisect import bisect_right
import math
import sqlite3
import threading
import time
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

with (Path(__file__).parent / "tz/Europe-London.tzif").open("rb") as tzfile:
    LONDON = ZoneInfo.from_file(tzfile, key="Europe/London")

DEVICES = {"shed": "Shed", "garage": "Garage"}
WINDOWS = {"24h": 86400, "7d": 604800, "30d": 2592000, "365d": 31536000, "live": 3600, "today": 86400,
           "yesterday": 86400, "previous24h": 86400}
FIELDS = ("voltage_v", "current_a", "power_w")


def period_bounds(window, now):
    if window in ("today", "yesterday"):
        midnight = datetime.fromtimestamp(now / 1000, LONDON).replace(
            hour=0, minute=0, second=0, microsecond=0)
        if window == "yesterday":
            return int((midnight - timedelta(days=1)).timestamp()*1000), int(midnight.timestamp()*1000)
        return int(midnight.timestamp()*1000), now
    end = now - 86400000 if window == "previous24h" else now
    return end - WINDOWS[window]*1000, end


def generation_bins(start, end, unit):
    """Small calendar-aligned bins clipped to the selected interval (UK days)."""
    if end <= start:
        return []
    local = datetime.fromtimestamp(start/1000, LONDON)
    if unit == "hour":
        boundaries = list(range((start//3600000+1)*3600000, end, 3600000))
    else:
        boundary = local.replace(hour=0, minute=0, second=0, microsecond=0)
        days = 1 if unit == "day" else 7
        if unit == "week":
            boundary -= timedelta(days=boundary.weekday())
        boundaries = []
        boundary += timedelta(days=days)
        while int(boundary.timestamp()*1000) < end:
            stamp = int(boundary.timestamp()*1000)
            if stamp > start:
                boundaries.append(stamp)
            boundary += timedelta(days=days)
    edges = [start, *boundaries, end]
    return [{"start":a, "end":b, "estimated_kwh":None,
             "coverage_s":0.0, "period_s":(b-a)/1000}
            for a,b in zip(edges,edges[1:])]


def positive_wh(a, b, milliseconds):
    if a >= 0 and b >= 0:
        average = (a+b)*.5
    elif a <= 0 and b <= 0:
        average = 0.0
    else:
        positive = max(a,b)
        average = positive*.5*positive/abs(b-a)
    return average*milliseconds/3600000


def estimate_energy(rows, start, end, plans=None):
    """Integrate signed power once, split at bin boundaries; never extrapolate."""
    previous = None
    wh, coverage = 0.0, 0.0
    plans = plans or {}
    limits = {key:[b['end'] for b in bins] for key,bins in plans.items()}
    for ts, power in rows:
        valid = isinstance(power, (int, float)) and math.isfinite(power)
        if previous is not None and valid:
            t0,p0 = previous
            dt = ts-t0
            left,right = max(start,t0),min(end,ts)
            if 0 < dt <= 1800000 and right > left:
                def at(t): return p0+(power-p0)*(t-t0)/dt
                wh += positive_wh(at(left),at(right),right-left)
                coverage += (right-left)/1000
                for key,bins in plans.items():
                    i = bisect_right(limits[key],left)
                    while i < len(bins) and bins[i]['start'] < right:
                        bucket=bins[i]
                        a,b=max(left,bucket['start']),min(right,bucket['end'])
                        bucket['estimated_kwh']=(bucket['estimated_kwh'] or 0)+positive_wh(at(a),at(b),b-a)/1000
                        bucket['coverage_s']+=(b-a)/1000
                        i+=1
        previous = (ts,power) if valid else None
    return {"estimated_kwh":wh/1000 if coverage else None,
            "coverage_s":coverage,"period_s":max(0,(end-start)/1000),
            "estimated":True,**plans}


class CachedTuya:
    def __init__(self, storage_directory, guard, clock=time.time, monotonic=time.monotonic,
                 database_uri=None):
        self.storage_directory = storage_directory
        self.guard = guard
        self.clock, self.monotonic = clock, monotonic
        self.database_uri = database_uri or (lambda fd:
            "file:/proc/self/fd/" + str(fd) + "/tuya.sqlite3?mode=ro")
        self.lock = threading.Lock()
        self.cache = {}

    def _read(self, key, ttl, query):
        # Shared bounded cache across clients: latest plus two devices per allowed period.
        with self.lock:
            cached = self.cache.get(key)
            if cached and self.monotonic() < cached[0]:
                if cached[1] is None:
                    raise RuntimeError("Stored Juwai readings unavailable")
                return copy.deepcopy(cached[1])
            try:
                with self.storage_directory() as directory:
                    self.guard()
                    db = sqlite3.connect(self.database_uri(directory), uri=True, timeout=1)
                    try:
                        db.execute("PRAGMA query_only=ON")
                        db.execute("PRAGMA cache_size=-128")
                        value = query(db)
                    finally:
                        db.close()
            except (OSError, RuntimeError, sqlite3.Error):
                # Do not keep serving a successful cache after a failed refresh.
                self.cache[key] = (self.monotonic() + 5, None)
                raise RuntimeError("Stored Juwai readings unavailable") from None
            self.cache[key] = (self.monotonic() + ttl, value)
            return copy.deepcopy(value)

    def latest(self):
        def query(db):
            devices = []
            for alias, name in DEVICES.items():
                row = db.execute("SELECT ts,voltage_v,current_a,power_w FROM samples "
                                 "WHERE device=? ORDER BY ts DESC LIMIT 1", (alias,)).fetchone()
                devices.append({"id": alias, "name": name,
                                "observed_at": row[0] if row else None,
                                "values": dict(zip(FIELDS, row[1:])) if row else
                                dict.fromkeys(FIELDS)})
            meta = db.execute("SELECT value FROM metadata WHERE name='collection_mode'").fetchone()
            return {"devices": devices, "collection_mode":
                    "automatic" if meta and meta[0] == "automatic" else "manual"}
        result = self._read("latest", 30, query)
        now = int(self.clock() * 1000)
        for device in result["devices"]:
            ts = device["observed_at"]
            age = max(0, (now - ts) // 1000) if ts is not None else None
            device.update(age_seconds=age, stale=ts is None or ts > now + 60000 or age > 1800)
        result.update(server_time=now, storage_available=True, expected_interval_seconds=900)
        return result

    def history(self, device, window):
        if device not in DEVICES or window not in WINDOWS:
            raise ValueError("Invalid Juwai history query")
        def query(db):
            end = int(self.clock() * 1000)
            start, end = period_bounds(window, end)
            period = 60 if WINDOWS[window] <= 604800 else 3600
            step = max(period * 1000,
                       math.ceil((end - start) / (999 * period * 1000)) * period * 1000)
            rows = db.execute("""SELECT CAST(bucket / ? AS INTEGER) * ?, SUM(n),
                SUM(voltage_sum) / NULLIF(SUM(voltage_n),0),
                SUM(current_sum) / NULLIF(SUM(current_n),0),
                SUM(power_sum) / NULLIF(SUM(power_n),0)
                FROM rollups WHERE device=? AND period=? AND bucket>=? AND bucket<=?
                GROUP BY CAST(bucket / ? AS INTEGER) ORDER BY 1 LIMIT 1000""",
                (step, step, device, period, start, end, step)).fetchall()
            energy = None
            if window != "365d":
                # Stream <=32 days of indexed minute groups; no raw history scan.
                cursor = db.execute("""SELECT bucket, power_sum / NULLIF(power_n,0)
                    FROM rollups WHERE device=? AND period=60 AND bucket>=? AND bucket<=?
                    ORDER BY bucket""", (device, start-1800000, min(int(self.clock()*1000), end+1800000)))
                unit = "week" if window == "30d" else "day" if window == "7d" else "hour"
                plans = {"buckets":generation_bins(start,end,unit)}
                if window == "30d":
                    plans["daily_buckets"] = generation_bins(start,end,"day")
                energy = estimate_energy(cursor,start,end,plans)
                energy["bucket_unit"] = unit
            return {"device": device, "window": window, "start": start, "end": end,
                    "energy": energy,
                    "step_ms": step, "points": [list(r) for r in rows],
                    "columns": ["ts", "samples", *FIELDS]}
        return self._read((device, window), 300, query)
