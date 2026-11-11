#!/usr/bin/env python3
"""Juwai meters read over the LAN (Tuya local protocol 3.3, 3.4 or 3.5). TEST collector.

Read-only: it only ever asks a meter for its status. It never sends a control
command, never pairs and never contacts the Tuya cloud. Keys, addresses and device
IDs live in private files on the Pi, never in this repository or in logs.
"""
import argparse
import binascii
import hashlib
import hmac
import ipaddress
import json
import logging
import math
import os
from pathlib import Path
import selectors
import signal
import socket
import sqlite3
import stat
import struct
import sys
import threading
import time

from tuya_cached import CachedTuya

LOG = logging.getLogger("juwai-local")
PORT = 6668
VERSIONS = ("3.3", "3.4", "3.5")
# Status queries and the session handshake only. No control command is defined here.
SESS_START, SESS_RESP, SESS_FINISH, DP_QUERY, DP_QUERY_NEW = 3, 4, 5, 0x0A, 0x10
# Same data points and scaling as the cloud fields cur_current, cur_power and cur_voltage.
DPS = {"voltage_v": ("20", 100.0), "current_a": ("18", 1000.0), "power_w": ("19", 100.0)}
STALE_SECONDS = 300
EXTRA_SCHEMA = """
CREATE TABLE IF NOT EXISTS read_stats (
    device TEXT NOT NULL, day TEXT NOT NULL, ok INTEGER NOT NULL, failed INTEGER NOT NULL,
    PRIMARY KEY(device, day)
);
CREATE TABLE IF NOT EXISTS events (ts INTEGER NOT NULL, device TEXT NOT NULL, kind TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS compare (
    device TEXT NOT NULL, cloud_ts INTEGER NOT NULL, local_ts INTEGER NOT NULL,
    cloud_voltage_v REAL, cloud_current_a REAL, cloud_power_w REAL,
    local_voltage_v REAL, local_current_a REAL, local_power_w REAL,
    PRIMARY KEY(device, cloud_ts)
);
"""


class LocalError(RuntimeError):
    """A failed local read. The message never contains a key, address or device ID."""


def _pad(data):
    n = 16 - len(data) % 16
    return data + bytes([n]) * n


def _unpad(data):
    if not data or not 1 <= data[-1] <= 16 or len(data) < data[-1]:
        raise LocalError("Bad padding")
    return data[:-data[-1]]


def _gcm(key):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM(key)


def _ecb(key, data, encrypt):
    # python3-cryptography (already on the Pi for passkeys), imported only when a meter is read.
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    op = cipher.encryptor() if encrypt else cipher.decryptor()
    return op.update(data) + op.finalize()


class Meter:
    """One short-lived, read-only connection to one meter."""

    def __init__(self, host, device_id, key, version, timeout=5, connect=socket.create_connection):
        if version not in VERSIONS:
            raise ValueError("Unsupported protocol version")
        self.host, self.device_id, self.version = host, device_id, version
        self.device_key = self.key = key.encode()
        if len(self.key) != 16:
            raise ValueError("Local key must be 16 characters")
        self.timeout, self._connect = timeout, connect
        self.sock, self.seq = None, 0

    def __enter__(self):
        try:
            self.sock = self._connect((self.host, PORT), self.timeout)
            self.sock.settimeout(self.timeout)
            if self.version != "3.3":
                self._negotiate()
        except LocalError:
            self.close()
            raise
        except OSError:
            self.close()
            raise LocalError("Meter unreachable") from None
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    # --- framing -------------------------------------------------------------
    def _send(self, cmd, payload):
        self.seq += 1
        if self.version == "3.5":
            nonce = os.urandom(12)
            header = struct.pack(">IHIII", 0x6699, 0, self.seq, cmd, len(payload) + 28)
            body = _gcm(self.key).encrypt(nonce, payload, header[4:])
            frame = header + nonce + body + struct.pack(">I", 0x9966)
        else:
            body = _ecb(self.key, _pad(payload), True)
            tail = 36 if self.version == "3.4" else 8
            frame = struct.pack(">4I", 0x55AA, self.seq, cmd, len(body) + tail) + body
            if self.version == "3.4":
                frame += hmac.new(self.key, frame, hashlib.sha256).digest()
            else:
                frame += struct.pack(">I", binascii.crc32(frame) & 0xFFFFFFFF)
            frame += struct.pack(">I", 0xAA55)
        self.sock.sendall(frame)

    def _read(self, count):
        data = b""
        while len(data) < count:
            chunk = self.sock.recv(count - len(data))
            if not chunk:
                raise LocalError("Meter closed the connection")
            data += chunk
        return data

    def _receive(self):
        """Return (command, decrypted payload) for the next frame."""
        prefix = self._read(4)
        try:
            if prefix == b"\x00\x00\x66\x99":
                rest = self._read(14)
                _, _, cmd, length = struct.unpack(">HIII", rest)
                if not 28 <= length <= 4096:
                    raise LocalError("Unexpected frame length")
                body = self._read(length + 4)[:-4]
                plain = _gcm(self.key).decrypt(body[:12], body[12:], rest)
            elif prefix == b"\x00\x00\x55\xaa":
                _, cmd, length = struct.unpack(">3I", self._read(12))
                tail = 36 if self.version == "3.4" else 8
                if not tail <= length <= 4096:
                    raise LocalError("Unexpected frame length")
                body = self._read(length)[:-tail]
                if len(body) >= 4 and body[:3] == b"\x00\x00\x00":
                    body = body[4:]  # return code
                if body[:3] == b"3.3":
                    body = body[15:]
                plain = _unpad(_ecb(self.key, body, False)) if body else b""
            else:
                raise LocalError("Unexpected frame")
        except LocalError:
            raise
        except Exception:
            # Wrong key or wrong protocol version both end up here.
            raise LocalError("Could not decrypt the meter's reply") from None
        if self.version == "3.5" and len(plain) >= 4 and plain[:3] == b"\x00\x00\x00":
            plain = plain[4:]  # return code
        if plain[:3] in (b"3.4", b"3.5"):
            plain = plain[15:]
        return cmd, plain

    def _negotiate(self):
        local = os.urandom(16)
        self._send(SESS_START, local)
        try:
            cmd, reply = self._receive()
        except socket.timeout:
            raise LocalError("No answer to the session handshake") from None
        if cmd != SESS_RESP or len(reply) < 48:
            raise LocalError("Session handshake refused")
        remote = reply[:16]
        if not hmac.compare_digest(hmac.new(self.key, local, hashlib.sha256).digest(), reply[16:48]):
            raise LocalError("Session handshake failed (key rejected)")
        self._send(SESS_FINISH, hmac.new(self.key, remote, hashlib.sha256).digest())
        mixed = bytes(a ^ b for a, b in zip(local, remote))
        if self.version == "3.4":
            self.key = _ecb(self.device_key, mixed, True)
        else:
            self.key = _gcm(self.device_key).encrypt(local[:12], mixed, None)[:16]

    # --- the only request ------------------------------------------------------
    def status(self):
        """Ask for the current data points. Returns {dp: value}."""
        query = json.dumps({"gwId": self.device_id, "devId": self.device_id,
                            "uid": self.device_id, "t": str(int(time.time()))},
                           separators=(",", ":")).encode()
        try:
            self._send(DP_QUERY if self.version == "3.3" else DP_QUERY_NEW, query)
            for _ in range(4):
                _, plain = self._receive()
                if not plain:
                    continue
                try:
                    obj = json.loads(plain)
                except ValueError:
                    raise LocalError("Meter refused the status query") from None
                if isinstance(obj, dict) and isinstance(obj.get("data"), dict):
                    obj = obj["data"]
                if isinstance(obj, dict) and isinstance(obj.get("dps"), dict):
                    return obj["dps"]
        except socket.timeout:
            raise LocalError("Meter did not answer in time") from None
        except OSError:
            raise LocalError("Connection to the meter was lost") from None
        raise LocalError("No status in the meter's reply")


def decode_dps(dps):
    """Validated readings with the cloud's scaling. Missing values stay None; nothing is guessed."""
    if not isinstance(dps, dict) or len(dps) > 128:
        raise ValueError("Unexpected status shape")
    raw = {}
    for dp, value in dps.items():
        if not isinstance(dp, str) or not dp.isdigit() or len(dp) > 4:
            raise ValueError("Invalid data point")
        if not isinstance(value, (str, int, float, bool, type(None))):
            raise ValueError("Unsupported status value")
        raw[dp] = value
    if len(json.dumps(raw, separators=(",", ":"))) > 8192:
        raise ValueError("Status payload too large")
    output = {}
    for field, (dp, scale) in DPS.items():
        value = raw.get(dp)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            output[field] = None
        else:
            output[field] = round(value / scale, 4)
    if all(value is None for value in output.values()):
        raise ValueError("No validated electrical readings")
    return output, raw


def read_meter(device, connect=socket.create_connection):
    with Meter(device["host"], device["id"], device["key"], device["version"], connect=connect) as meter:
        return decode_dps(meter.status())


# --- finding a meter again after its address changes ---------------------------
def open_hosts(network, port=PORT, timeout=2.0, batch=100):
    """Addresses on the Pi's own network with the local port open. LAN only."""
    network = ipaddress.ip_network(network, strict=False)
    if not network.is_private or network.num_addresses > 1024:
        raise ValueError("Refusing to scan anything but a small private network")
    hosts, found = [str(h) for h in network.hosts()], []
    for i in range(0, len(hosts), batch):
        sel = selectors.DefaultSelector()
        for host in hosts[i:i + batch]:
            sock = socket.socket()
            sock.setblocking(False)
            try:
                sock.connect_ex((host, port))
                sel.register(sock, selectors.EVENT_WRITE, host)
            except OSError:
                sock.close()
        end = time.monotonic() + timeout
        while sel.get_map() and time.monotonic() < end:
            for key, _ in sel.select(0.3):
                if key.fileobj.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR) == 0:
                    found.append(key.data)
                sel.unregister(key.fileobj)
                key.fileobj.close()
        for key in list(sel.get_map().values()):
            key.fileobj.close()
        sel.close()
    return found


def find_meter(device, network, versions=VERSIONS):
    """Find one meter by proving it holds our key. Returns (host, version) or None."""
    for host in open_hosts(network):
        for version in ([device["version"]] if device.get("version") else []) + \
                [v for v in versions if v != device.get("version")]:
            try:
                read_meter({**device, "host": host, "version": version})
                return host, version
            except (LocalError, ValueError):
                time.sleep(1)  # be gentle: these are small devices
    return None


# --- private configuration --------------------------------------------------
def load_config(path):
    """{"interval_seconds": 60, "keys_file": "...", "network": "a.b.c.d/nn",
        "devices": {"shed": {"host": "...", "version": "3.4"}, ...}}"""
    p = Path(path)
    if stat.S_IMODE(p.stat().st_mode) & 0o077:
        raise RuntimeError("Local Juwai config must be private (chmod 600)")
    conf = json.loads(p.read_text())
    interval = conf.get("interval_seconds", 60)
    if type(interval) is not int or not 10 <= interval <= 3600:
        raise ValueError("interval_seconds must be between 10 and 3600")
    keys_path = Path(conf.get("keys_file", p.with_name("tuya-local-keys.json")))
    if stat.S_IMODE(keys_path.stat().st_mode) & 0o077:
        raise RuntimeError("Local keys file must be private (chmod 600)")
    keys = json.loads(keys_path.read_text())["devices"]
    devices = {}
    for alias, entry in (conf.get("devices") or {}).items():
        if alias not in ("shed", "garage") or alias not in keys:
            raise ValueError("Unknown device alias")
        if entry.get("version") not in VERSIONS:
            raise ValueError("Set each meter's protocol version")
        ipaddress.ip_address(entry["host"])
        devices[alias] = {"id": keys[alias]["id"], "key": keys[alias]["local_key"],
                          "host": entry["host"], "version": entry["version"]}
    if not devices:
        raise ValueError("No devices configured")
    return {"interval": interval, "devices": devices, "network": conf.get("network"), "path": p}


def save_host(conf, alias, host, version):
    """Remember a meter's new address in the private config (atomic, mode 0600)."""
    data = json.loads(conf["path"].read_text())
    data["devices"][alias].update(host=host, version=version)
    tmp = conf["path"].with_name(conf["path"].name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(data, handle, indent=1)
    os.replace(tmp, conf["path"])


# --- reading stored local samples for the page (never contacts a meter) ---------
class CachedLocal(CachedTuya):
    """The same bounded, cached, read-only queries as the cloud history, on the test database."""

    def __init__(self, storage_directory, guard, **kwargs):
        kwargs.setdefault("database_uri", lambda fd:
                          "file:/proc/self/fd/" + str(fd) + "/juwai_local.sqlite3?mode=ro")
        super().__init__(storage_directory, guard, **kwargs)

    def latest(self):
        result = super().latest()
        now = result["server_time"]
        for device in result["devices"]:
            ts = device["observed_at"]
            device["stale"] = ts is None or ts > now + 60000 or device["age_seconds"] > STALE_SECONDS
        result["expected_interval_seconds"] = 60
        return result


# --- collection -------------------------------------------------------------
def note(store, device, ok, kind=None, now=None):
    now = time.time() if now is None else now
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    with store.db:
        store.db.execute("""INSERT INTO read_stats VALUES (?,?,?,?)
            ON CONFLICT(device,day) DO UPDATE SET ok=ok+excluded.ok, failed=failed+excluded.failed""",
            (device, day, int(ok), int(not ok)))
        if kind:
            store.db.execute("INSERT INTO events VALUES (?,?,?)", (int(now * 1000), device, kind))
            store.db.execute("DELETE FROM events WHERE ts<?", (int(now * 1000) - 60 * 86400000,))


def compare_with_cloud(store, cloud_uri, devices, window_ms=90000):
    """Background accuracy log: pair each new cloud sample with the nearest local one.

    Opens the production cloud history strictly read-only. Never shown on the page.
    """
    try:
        cloud = sqlite3.connect(cloud_uri, uri=True, timeout=1)
    except sqlite3.Error:
        return
    try:
        cloud.execute("PRAGMA query_only=ON")
        for device in devices:
            done = store.db.execute("SELECT MAX(cloud_ts) FROM compare WHERE device=?", (device,)).fetchone()[0]
            since = max(done or 0, int(time.time() * 1000) - 86400000)
            rows = cloud.execute("SELECT ts,voltage_v,current_a,power_w FROM samples "
                                 "WHERE device=? AND ts>? ORDER BY ts LIMIT 200", (device, since)).fetchall()
            for ts, *values in rows:
                if ts > int(time.time() * 1000) - window_ms:
                    break  # a closer local sample may still arrive
                near = store.db.execute("""SELECT ts,voltage_v,current_a,power_w FROM samples
                    WHERE device=? AND ts BETWEEN ? AND ? ORDER BY ABS(ts-?) LIMIT 1""",
                    (device, ts - window_ms, ts + window_ms, ts)).fetchone()
                if near:
                    with store.db:
                        store.db.execute("INSERT OR IGNORE INTO compare VALUES (?,?,?,?,?,?,?,?,?)",
                                         (device, ts, near[0], *values, *near[1:]))
    except sqlite3.Error:
        pass  # the comparison is best effort and must never stop local reads
    finally:
        cloud.close()


def run(conf, store, stop, cloud_uri=None, read=read_meter, find=find_meter):
    failures = dict.fromkeys(conf["devices"], 0)
    last_search = dict.fromkeys(conf["devices"], -math.inf)
    store.metadata("collection_mode", "automatic")
    while not stop.is_set():
        started = time.monotonic()
        for alias, device in conf["devices"].items():
            if stop.is_set():
                break
            try:
                output, raw = read(device)
                ts = int(time.time() * 1000)
                store.add(alias, ts, output, raw)
                note(store, alias, True, "recovered" if failures[alias] >= 3 else None)
                failures[alias] = 0
            except (LocalError, ValueError) as exc:
                failures[alias] += 1
                note(store, alias, False, "failing" if failures[alias] == 3 else None)
                if failures[alias] in (1, 3) or failures[alias] % 60 == 0:
                    LOG.warning("Local read of %s failed (%s), %d in a row", alias, exc, failures[alias])
                # After five misses look once for a new address; at most hourly.
                if (conf.get("network") and failures[alias] >= 5
                        and time.monotonic() - last_search[alias] >= 3600):
                    last_search[alias] = time.monotonic()
                    try:
                        hit = find(device, conf["network"])
                    except (ValueError, OSError):
                        hit = None
                    if hit and hit[0] != device["host"]:
                        device["host"], device["version"] = hit
                        save_host(conf, alias, *hit)
                        note(store, alias, True, "address_changed")
                        LOG.warning("%s answered at a new address; private config updated", alias)
        if cloud_uri:
            compare_with_cloud(store, cloud_uri, conf["devices"])
        stop.wait(max(1.0, conf["interval"] - (time.monotonic() - started)))


def main(storage_directory, guard):
    from tuya_collector import USBStore, process_lock  # same sample and rollup tables, separate file
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/var/lib/modpi/private/juwai-local.json")
    parser.add_argument("--lock-file", default="/var/lib/modpi/private/juwai-local.lock")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--probe", action="store_true", help="Read each meter once; store nothing")
    mode.add_argument("--run", action="store_true", help="Test collector: read on a schedule")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    conf = load_config(args.config)
    with process_lock(args.lock_file):
        if args.probe:
            good = 0
            for alias, device in conf["devices"].items():
                try:
                    output, raw = read_meter(device)
                    print(json.dumps({"device": alias, "values": output, "dps": raw}, separators=(",", ":")))
                    good += 1
                except (LocalError, ValueError) as exc:
                    print(json.dumps({"device": alias, "error": str(exc)}))
            raise SystemExit(0 if good == len(conf["devices"]) else 1)
        with storage_directory() as fd:
            base = "/proc/self/fd/" + str(fd)
            store = USBStore(base + "/juwai_local.sqlite3", guard)
            store.db.executescript(EXTRA_SCHEMA)
            stop = threading.Event()
            signal.signal(signal.SIGTERM, lambda *_: stop.set())
            signal.signal(signal.SIGINT, lambda *_: stop.set())
            try:
                run(conf, store, stop, cloud_uri="file:" + base + "/tuya.sqlite3?mode=ro")
            finally:
                store.metadata("collection_mode", "manual")
                store.close()


if __name__ == "__main__":
    # Pi entrypoint (modpi-juwai-local.service): Pi microSD storage guard, as pi/tuya_run.py.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pi"))
    from storage import Storage
    _storage = Storage()
    main(_storage.directory, _storage.check)
