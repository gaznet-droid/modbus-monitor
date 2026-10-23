"""Bounded read-only USB queries. This module never contacts Tuya or devices."""
import copy
import math
import sqlite3
import threading
import time

DEVICES = {"shed": "Shed", "garage": "Garage"}
WINDOWS = {"24h": 86400, "7d": 604800, "30d": 2592000, "365d": 31536000}
FIELDS = ("voltage_v", "current_a", "power_w")


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
        # One shared lock/cache across all HTTP clients; at most nine cache keys.
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
            start = end - WINDOWS[window] * 1000
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
            return {"device": device, "window": window, "start": start, "end": end,
                    "step_ms": step, "points": [list(r) for r in rows],
                    "columns": ["ts", "samples", *FIELDS]}
        return self._read((device, window), 300, query)
