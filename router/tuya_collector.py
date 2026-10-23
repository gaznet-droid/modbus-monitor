#!/usr/bin/env python3
"""Modbus project: independent, read-only Tuya energy samples. Never sends commands.

Requires a private /etc/modbus-tuya.json (mode 0600) and the existing dedicated
USB guard for storage. This file is deliberately NOT registered as a service.
"""
import argparse
import hashlib
import hmac
import json
import logging
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LOG = logging.getLogger("modbus-tuya")
API = "https://openapi.tuyaeu.com"  # Existing Central Europe project ONLY.
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
DEVICE_ID = re.compile(r"^[A-Za-z0-9]{10,40}$")
DP_SCALE = {"cur_voltage": 100.0, "cur_current": 1000.0, "cur_power": 100.0}
SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    device TEXT NOT NULL, ts INTEGER NOT NULL,
    voltage_v REAL, current_a REAL, power_w REAL,
    raw_json TEXT NOT NULL, PRIMARY KEY(device, ts)
);
CREATE TABLE IF NOT EXISTS rollups (
    device TEXT NOT NULL, period INTEGER NOT NULL, bucket INTEGER NOT NULL,
    n INTEGER NOT NULL, voltage_sum REAL NOT NULL, voltage_n INTEGER NOT NULL,
    current_sum REAL NOT NULL, current_n INTEGER NOT NULL,
    power_sum REAL NOT NULL, power_n INTEGER NOT NULL,
    PRIMARY KEY(device,period,bucket)
);
CREATE TABLE IF NOT EXISTS metadata (name TEXT PRIMARY KEY,value TEXT NOT NULL);
PRAGMA user_version=1;
"""

class TuyaError(RuntimeError):
    def __init__(self, code):
        self.code = str(code)
        super().__init__(self.code)


def decode_status(items):
    """Only return validated raw DPs and derived *measured* units; no guessed data."""
    if not isinstance(items, list) or len(items) > 128:
        raise ValueError("Unexpected status shape")
    raw = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("code"), str):
            raise ValueError("Invalid status entry")
        code = item["code"]
        if not re.fullmatch(r"[a-zA-Z_0-9]{1,64}", code):
            raise ValueError("Invalid status code")
        if code in raw:
            raise ValueError("Duplicate status code")
        value = item.get("value")
        if not isinstance(value, (str, int, float, bool, type(None))):
            raise ValueError("Unsupported status value")
        raw[code] = value
    if len(json.dumps(raw, separators=(",", ":"))) > 8192:
        raise ValueError("Status payload too large")
    output = {}
    for code, field in (("cur_voltage", "voltage_v"),
                        ("cur_current", "current_a"),
                        ("cur_power", "power_w")):
        value = raw.get(code)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            output[field] = None
        else:
            output[field] = round(value / DP_SCALE[code], 4)
    if all(value is None for value in output.values()):
        raise ValueError("No validated electrical readings")
    return output, raw


class TuyaCloud:
    def __init__(self, client_id, client_secret, transport=urlopen):
        self.client_id = client_id
        self._secret = client_secret
        self.transport = transport
        self.access_token = None
        self.expires_at = 0

    def get(self, path, *, authenticated=False):
        # Explicit allowlist: this integration cannot send commands or write APIs.
        if path != "/v1.0/token?grant_type=1" and not re.fullmatch(
                r"/v1\.0/devices/[A-Za-z0-9]{10,40}/status", path):
            raise ValueError("Unsupported read-only endpoint")
        ts = str(int(time.time() * 1000))
        token = self.access_token if authenticated else ""
        if authenticated and not token:
            raise TuyaError("Missing token")
        canonical = "GET\n" + EMPTY_SHA256 + "\n\n" + path
        digest = hmac.new(self._secret.encode(),
                          (self.client_id + token + ts + canonical).encode(),
                          hashlib.sha256).hexdigest().upper()
        headers = {"client_id": self.client_id, "t": ts, "sign": digest,
                   "sign_method": "HMAC-SHA256"}
        if token:
            headers["access_token"] = token
        req = Request(API + path, headers=headers, method="GET")
        try:
            with self.transport(req, timeout=12) as response:
                obj = json.loads(response.read(100000))
        except HTTPError as exc:
            raise TuyaError("HTTP-" + str(exc.code)) from None
        except (URLError, TimeoutError, OSError):
            raise TuyaError("Network unavailable") from None
        except (ValueError, UnicodeError):
            raise TuyaError("Invalid cloud response") from None
        if not isinstance(obj, dict) or not obj.get("success"):
            raise TuyaError(obj.get("code", "Unexpected response") if isinstance(obj, dict) else "Unexpected response")
        return obj

    def token(self):
        if self.access_token and time.monotonic() < self.expires_at:
            return
        obj = self.get("/v1.0/token?grant_type=1")
        result = obj.get("result") or {}
        token = result.get("access_token")
        expire = result.get("expire_time", 7200)
        if not isinstance(token, str) or not token or not isinstance(expire, (int, float)):
            raise TuyaError("Invalid token response")
        self.access_token = token
        self.expires_at = time.monotonic() + max(60, min(float(expire) - 300, 3600))

    def status(self, device_id):
        if not DEVICE_ID.fullmatch(device_id):
            raise ValueError("Invalid device ID")
        self.token()
        try:
            obj = self.get("/v1.0/devices/" + device_id + "/status", authenticated=True)
        except TuyaError as exc:
            if exc.code in ("1010", "1011"):
                self.access_token = None
                self.token()
                obj = self.get("/v1.0/devices/" + device_id + "/status", authenticated=True)
            else:
                raise
        return decode_status(obj.get("result"))


class USBStore:
    def __init__(self, path, guard):
        self.guard = guard
        self.guard()
        self.db = sqlite3.connect(path, timeout=5)
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA cache_size=-512")
        self.db.executescript(SCHEMA)
        self.last_cleanup = 0

    def add(self, device, ts, output, raw):
        self.guard()  # Fail closed if the dedicated USB is absent/remounted.
        with self.db:
            self.db.execute("""INSERT OR IGNORE INTO samples
                (device,ts,voltage_v,current_a,power_w,raw_json)
                VALUES (?,?,?,?,?,?)""", (device,ts,output["voltage_v"],
                output["current_a"],output["power_w"],json.dumps(raw, separators=(",", ":"))))
            if not self.db.execute("SELECT changes()").fetchone()[0]:
                return
            for period in (60, 3600):
                bucket = ts // (period * 1000) * (period * 1000)
                args = []
                for key in ("voltage_v", "current_a", "power_w"):
                    val = output[key]
                    args.extend((val if val is not None else 0.0, int(val is not None)))
                self.db.execute("""INSERT INTO rollups
                    (device,period,bucket,n,voltage_sum,voltage_n,current_sum,current_n,power_sum,power_n)
                    VALUES (?,?,?,1,?,?,?,?,?,?)
                    ON CONFLICT(device,period,bucket) DO UPDATE SET
                    n=n+1, voltage_sum=voltage_sum+excluded.voltage_sum,
                    voltage_n=voltage_n+excluded.voltage_n,
                    current_sum=current_sum+excluded.current_sum,
                    current_n=current_n+excluded.current_n,
                    power_sum=power_sum+excluded.power_sum,
                    power_n=power_n+excluded.power_n""", (device,period,bucket,*args))
            if ts - self.last_cleanup >= 3600000:
                self.db.execute("DELETE FROM samples WHERE rowid IN (SELECT rowid FROM samples WHERE ts<? LIMIT 5000)", (ts - 7 * 86400000,))
                self.db.execute("DELETE FROM rollups WHERE rowid IN (SELECT rowid FROM rollups WHERE period=60 AND bucket<? LIMIT 5000)", (ts - 365 * 86400000,))
                self.last_cleanup = ts

    def close(self):
        self.db.close()


def load_config(path):
    p = Path(path)
    mode = stat.S_IMODE(p.stat().st_mode)
    if mode & 0o077:
        raise RuntimeError("Tuya config must be private (chmod 600)")
    c = json.loads(p.read_text())
    if not (isinstance(c.get("client_id"), str) and isinstance(c.get("client_secret"), str)):
        raise ValueError("Missing Tuya credentials")
    interval = c.get("interval_seconds", 900)
    if not isinstance(interval, int) or isinstance(interval, bool) or interval < 900:
        raise ValueError("Cloud interval must be >=900 seconds")
    devices = c.get("devices")
    if not isinstance(devices, dict) or not 1 <= len(devices) <= 3:
        raise ValueError("Expected one to three named devices")
    for label, identifier in devices.items():
        if not isinstance(label, str) or not re.fullmatch(r"[a-z0-9_-]{1,24}", label):
            raise ValueError("Invalid device alias")
        if not isinstance(identifier, str) or not DEVICE_ID.fullmatch(identifier):
            raise ValueError("Invalid device ID")
    return c


def collect_once(cloud, devices, store=None):
    ok = 0
    for label, ident in devices.items():
        try:
            output, raw = cloud.status(ident)
            ts = int(time.time() * 1000)
            if store is not None:
                store.add(label,ts,output,raw)
            print(json.dumps({"device":label,"ts":ts,"values":output},separators=(",", ":")))
            ok += 1
        except (TuyaError, ValueError) as exc:
            LOG.warning("Read-only status for %s unavailable (%s)",label,
                        exc.code if isinstance(exc,TuyaError) else "Invalid telemetry")
    return ok


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",default="/etc/modbus-tuya.json")
    parser.add_argument("--probe",action="store_true",help="GET only; no USB writes")
    parser.add_argument("--once",action="store_true",help="GET then write one set to guarded USB")
    args=parser.parse_args()
    if args.probe and args.once:
        parser.error("Choose either --probe or --once")
    logging.basicConfig(level=logging.INFO,format="%(levelname)s %(message)s")
    conf=load_config(args.config)
    cloud=TuyaCloud(conf["client_id"],conf["client_secret"])
    if args.probe:
        raise SystemExit(0 if collect_once(cloud,conf["devices"]) else 1)
    sys.path.insert(0,"/usr/local/usr/lib/clockmill")
    import modbus_usb
    # Reuse the original Modbus USB mount/identity guard, with no disk fallback.
    with modbus_usb.storage_directory() as fd:
        store=USBStore("/proc/self/fd/"+str(fd)+"/tuya.sqlite3",modbus_usb.check)
        try:
            while True:
                started=time.monotonic()
                collect_once(cloud,conf["devices"],store)
                if args.once: return
                time.sleep(max(1,conf["interval_seconds"]-(time.monotonic()-started)))
        finally:
            store.close()

if __name__ == "__main__":
    main()
