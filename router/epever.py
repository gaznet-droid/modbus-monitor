#!/usr/bin/env python3
"""EPEVER Tracer-AN solar charger, read by listening only.

The charger's Wi-Fi dongle keeps its own link to EPEVER's server (the Solar Guardian
app) and, on a second connection, copies the charger's Modbus RTU replies to this
collector. This module never sends a byte to the dongle or the charger: it accepts
the connection, reads, and stores. It cannot switch the load or change a setting.

Only replies arrive, not the requests, so each reply is recognised by its length and
its place in the burst. Registers and scaling are from EPEVER's "LS-B, VS-B, Tracer-B,
Tracer-A ... Controller Communication Instruction" (V2.5): 32-bit values are sent low
word first, and volts, amps, watts, degrees and kWh are all multiplied by 100.

    replies of 4 words   3100-3103  PV voltage, current, power
    replies of 6 words   310C-3111  load voltage, current, power; battery and device temperature
    replies of 3 words   3200-3202  battery, charging and discharging status
    replies of 18 words  3302-3313  day's battery extremes, consumed and generated energy
    one word of 0..100 after a status or load reply   311A  battery state of charge
    one or two words starting above 8 V               331A (331B)  battery voltage (and current)

The dongle's serial number and the Pi's address are private and are never stored here.
"""
import argparse
import copy
import json
import logging
import math
from pathlib import Path
import selectors
import signal
import socket
import sqlite3
import struct
import sys
import threading
import time

from tuya_cached import WINDOWS, period_bounds

LOG = logging.getLogger("epever")
UNIT = 1
BURST_GAP = 2.0           # seconds of silence that end a burst of replies
STALE_SECONDS = 900       # PV power normally arrives every few minutes
CHARGE_STATES = ("Not charging", "Float", "Boost", "Equalising")
FIELDS = ("pv_v", "pv_a", "pv_w", "batt_v", "batt_a", "soc", "charge_state", "load_on", "load_w",
          "batt_temp_c", "device_temp_c", "gen_today_kwh", "gen_total_kwh", "batt_v_max_today",
          "batt_v_min_today")
DAILY = ("gen_today_kwh", "batt_v_max_today", "batt_v_min_today")  # the charger restarts these each day
ADDED = ("batt_v_max_today", "batt_v_min_today")  # columns added after 14 Nov 2026; older tables lack them
SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    ts INTEGER PRIMARY KEY, pv_v REAL, pv_a REAL, pv_w REAL, batt_v REAL, batt_a REAL, soc INTEGER,
    charge_state INTEGER, load_on INTEGER, load_w REAL, batt_temp_c REAL, device_temp_c REAL,
    gen_today_kwh REAL, gen_total_kwh REAL, status TEXT, batt_v_max_today REAL, batt_v_min_today REAL
);
CREATE TABLE IF NOT EXISTS metadata (name TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def crc16(data):
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return struct.pack("<H", crc)


class Frames:
    """Splits the byte stream into the charger's read replies (functions 01 to 04), CRC checked."""

    def __init__(self, unit=UNIT):
        self.unit, self.buffer = unit, b""

    def feed(self, data):
        self.buffer = (self.buffer + data)[-1024:]
        found, i, buf = [], 0, self.buffer
        while i + 5 <= len(buf):
            count = buf[i + 2]
            end = i + 3 + count + 2
            if buf[i] == self.unit and 1 <= buf[i + 1] <= 4 and count:
                if end > len(buf):
                    if count <= 125:
                        break  # possibly the start of a reply still arriving
                elif crc16(buf[i:end - 2]) == buf[end - 2:end]:
                    payload = buf[i + 3:end - 2]
                    if buf[i + 1] >= 3 and count % 2 == 0:
                        found.append(list(struct.unpack(">%dH" % (count // 2), payload)))
                    i = end
                    continue
            i += 1
        self.buffer = buf[i:]
        return found


def _signed(word):
    return word - 0x10000 if word & 0x8000 else word


def _long(low, high):
    return (high << 16 | low) / 100.0


class Burst:
    """Readings gathered from one burst of replies."""

    def __init__(self):
        self.values, self.status, self.previous = {}, {}, 0

    def add(self, words):
        n, v = len(words), self.values
        if n == 4:
            v.update(pv_v=words[0] / 100.0, pv_a=words[1] / 100.0, pv_w=_long(words[2], words[3]))
        elif n == 6:
            v.update(load_w=_long(words[2], words[3]), batt_temp_c=_signed(words[4]) / 100.0,
                     device_temp_c=_signed(words[5]) / 100.0)
            v.setdefault("load_on", int(words[0] > 100))
        elif n == 18:
            v.update(batt_v_max_today=words[0] / 100.0, batt_v_min_today=words[1] / 100.0,
                     gen_today_kwh=_long(words[10], words[11]), gen_total_kwh=_long(words[16], words[17]))
        elif n == 3:
            self._status(words[0], words[1])
            self.status["discharging"] = words[2]
            v["load_on"] = words[2] & 1
        elif n == 2 and words[0] >= 800:
            v.update(batt_v=words[0] / 100.0, batt_a=_signed(words[1]) / 100.0)
        elif n == 2:
            self._status(words[0], words[1])
        elif n == 1 and words[0] >= 800:
            v["batt_v"] = words[0] / 100.0
        elif n == 1 and self.previous == 18:
            v["batt_a"] = _signed(words[0]) / 100.0
        elif n == 1 and self.previous in (3, 6) and words[0] <= 100:
            v["soc"] = words[0]
        self.previous = n

    def _status(self, battery, charging):
        self.status.update(battery=battery, charging=charging)
        self.values["charge_state"] = (charging >> 2) & 3


class Store:
    def __init__(self, path, guard):
        self.guard = guard
        self.guard()
        self.db = sqlite3.connect(path, timeout=5)
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA cache_size=-256")
        self.db.executescript(SCHEMA)
        have = {row[1] for row in self.db.execute("PRAGMA table_info(samples)")}
        with self.db:
            for name in ADDED:
                if name not in have:
                    self.db.execute("ALTER TABLE samples ADD COLUMN %s REAL" % name)

    def add(self, ts, burst):
        if not burst.values:
            return
        self.guard()
        row = [burst.values.get(name) for name in FIELDS]
        status = json.dumps(burst.status, separators=(",", ":"), sort_keys=True) if burst.status else None
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO samples (ts,%s,status) VALUES (?%s,?)"
                            % (",".join(FIELDS), ",?" * len(FIELDS)), (ts, *row, status))

    def metadata(self, name, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", (name, str(value)))

    def close(self):
        self.db.close()


def listen(server, store, stop, clock=time.time, gap=BURST_GAP):
    """Accept the dongle's connection and store what it sends. Nothing is ever written to it."""
    sel = selectors.DefaultSelector()
    sel.register(server, selectors.EVENT_READ)
    conn, frames, burst, last = None, Frames(), Burst(), 0.0

    def flush():
        nonlocal burst
        try:
            store.add(int(clock() * 1000), burst)
        except (OSError, RuntimeError, sqlite3.Error) as exc:
            LOG.warning("Reading not stored: %s", exc.__class__.__name__)
        burst = Burst()

    def drop():
        nonlocal conn, frames
        if conn is not None:
            sel.unregister(conn)
            conn.close()
            conn, frames = None, Frames()

    try:
        while not stop.is_set():
            for key, _ in sel.select(timeout=0.5):
                if key.fileobj is server:
                    new, _ = server.accept()
                    drop()  # the dongle reconnected; the newest connection is the live one
                    new.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                    conn = new
                    sel.register(conn, selectors.EVENT_READ)
                    LOG.info("Charger dongle connected")
                    continue
                try:
                    data = conn.recv(4096)
                except OSError:
                    data = b""
                if not data:
                    LOG.info("Charger dongle disconnected")
                    drop()
                    continue
                for words in frames.feed(data):
                    burst.add(words)
                    last = time.monotonic()
            if burst.values and time.monotonic() - last >= gap:
                flush()
                frames.buffer = b""  # silence: no reply can still be half arrived
    finally:
        flush()
        drop()
        sel.close()


def load_config(path):
    conf = json.loads(Path(path).read_text())
    port = conf.get("port", 15001)
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("port must be between 1024 and 65535")
    name = conf.get("name", "Epever")
    if not isinstance(name, str) or not 1 <= len(name) <= 40:
        raise ValueError("name must be a short label")
    return {"port": port, "name": name, "bind": str(conf.get("bind", "0.0.0.0"))}


# --- read-only queries for the page -------------------------------------------
class CachedEpever:
    """Bounded, cached, read-only queries on the stored readings. Never contacts the dongle."""

    def __init__(self, storage_directory, guard, clock=time.time, monotonic=time.monotonic,
                 database_uri=None):
        self.storage_directory, self.guard = storage_directory, guard
        self.clock, self.monotonic = clock, monotonic
        self.database_uri = database_uri or (lambda fd:
            "file:/proc/self/fd/" + str(fd) + "/epever.sqlite3?mode=ro")
        self.lock = threading.Lock()
        self.cache = {}

    def _read(self, key, ttl, query):
        with self.lock:
            cached = self.cache.get(key)
            if cached and self.monotonic() < cached[0]:
                return copy.deepcopy(cached[1])
            value = None  # None: no collector has ever stored a reading here
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
                ttl = 15
            self.cache[key] = (self.monotonic() + ttl, value)
            return copy.deepcopy(value)

    def latest(self):
        def query(db):
            values, times = {}, {}
            have = {row[1] for row in db.execute("PRAGMA table_info(samples)")}
            for name in FIELDS:
                if name not in have:  # the collector has not added this column yet
                    values[name] = times[name] = None
                    continue
                row = db.execute("SELECT ts,%s FROM samples WHERE %s IS NOT NULL ORDER BY ts DESC LIMIT 1"
                                 % (name, name)).fetchone()
                values[name], times[name] = (row[1], row[0]) if row else (None, None)
            name = db.execute("SELECT value FROM metadata WHERE name='name'").fetchone()
            return {"name": name[0] if name else "Epever", "values": values, "times": times}
        result = self._read("latest", 20, query)
        if result is None:
            return None
        now = int(self.clock() * 1000)
        midnight = period_bounds("today", now)[0]
        for name in DAILY:  # yesterday's figure is not today's: show nothing until today's arrives
            if result["times"][name] is not None and result["times"][name] < midnight:
                result["values"][name] = None
        state = result["values"]["charge_state"]
        result["values"]["charge_state_name"] = CHARGE_STATES[state] if state in (0, 1, 2, 3) else None
        ts = result["times"]["pv_w"]
        age = max(0, (now - ts) // 1000) if ts is not None else None
        seen = [t for t in result["times"].values() if t is not None]
        result.update(observed_at=ts, age_seconds=age, last_heard_at=max(seen) if seen else None,
                      stale=ts is None or ts > now + 60000 or age > STALE_SECONDS, server_time=now)
        return result

    def history(self, window):
        if window not in WINDOWS:
            raise ValueError("Invalid Epever history query")
        def query(db):
            start, end = period_bounds(window, int(self.clock() * 1000))
            step = max(60000, math.ceil((end - start) / 720 / 60000) * 60000)
            buckets = {}
            for ts, *row in db.execute("SELECT ts,pv_w,batt_v,batt_a FROM samples "
                                       "WHERE ts>=? AND ts<=? ORDER BY ts", (start, end)):
                sums = buckets.setdefault(ts // step * step, [0.0, 0, 0.0, 0, 0.0, 0])
                for i, value in enumerate(row):
                    if value is not None:
                        sums[i * 2] += value
                        sums[i * 2 + 1] += 1
            points = [[bucket, *(round(s[i] / s[i + 1], 2) if s[i + 1] else None for i in (0, 2, 4))]
                      for bucket, s in sorted(buckets.items())]
            # Generation comes from the charger's own lifetime counter, not from integrating power.
            counter = "SELECT gen_total_kwh FROM samples WHERE gen_total_kwh IS NOT NULL AND ts%s? ORDER BY ts %s LIMIT 1"
            last = db.execute(counter % ("<=", "DESC"), (end,)).fetchone()
            base = db.execute(counter % ("<", "DESC"), (start,)).fetchone()
            partial = base is None  # no reading from before the period: count from the first one in it
            if partial:
                base = db.execute(counter % (">=", "ASC"), (start,)).fetchone()
            generated = round(last[0] - base[0], 2) if last and base and last[0] >= base[0] else None
            return {"window": window, "start": start, "end": end, "step_ms": step, "points": points,
                    "columns": ["ts", "pv_w", "batt_v", "batt_a"],
                    "energy": {"generated_kwh": generated, "partial": partial, "estimated": False}}
        return self._read(window, 120, query)


def main(storage_directory, guard):
    from tuya_collector import process_lock
    parser = argparse.ArgumentParser(description="Epever charger: listen-only collector")
    parser.add_argument("--config", default="/var/lib/modpi/private/epever.json")
    parser.add_argument("--lock-file", default="/var/lib/modpi/private/epever.lock")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    conf = load_config(args.config)
    with process_lock(args.lock_file), storage_directory() as fd:
        store = Store("/proc/self/fd/" + str(fd) + "/epever.sqlite3", guard)
        store.metadata("name", conf["name"])
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((conf["bind"], conf["port"]))
        server.listen(2)
        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        LOG.info("Listening for the charger dongle")
        try:
            listen(server, store, stop)
        finally:
            server.close()
            store.close()


if __name__ == "__main__":
    # Pi entrypoint (modpi-epever.service): Pi microSD storage guard, as the other collectors.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pi"))
    from storage import Storage
    _storage = Storage()
    main(_storage.directory, _storage.check)
