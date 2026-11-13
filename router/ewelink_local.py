#!/usr/bin/env python3
"""eWeLink power plugs read over the LAN (Sonoff local protocol). Read-only.

The plugs announce their state by mDNS (_ewelink._tcp) with the params encrypted
by each device's key, and answer encrypted HTTP on port 8081. This module only
ever listens, sends standard mDNS questions, and asks a plug for its state with
an empty request body. No control command or setting is defined here.
Keys, addresses and device IDs live in private files on the Pi, never in this
repository or in logs.
"""
import base64
import contextlib
import hashlib
import json
import logging
import os
import socket
import sqlite3
import struct
import threading
import time
import urllib.request

LOG = logging.getLogger("ewelink-local")

MDNS_GROUP, MDNS_PORT = "224.0.0.251", 5353
SERVICE = "_ewelink._tcp.local"
HTTP_PORT = 8081
# Empty-body state reads only. Anything else is refused before it can be sent.
READ_COMMANDS = ("info", "getState")
T_A, T_PTR, T_TXT, T_SRV = 1, 12, 16, 33


class LocalError(RuntimeError):
    """A failed local read. The message never contains a key, address or device ID."""


def _cipher(devicekey, iv):
    # python3-cryptography (already on the Pi for passkeys), imported only when used.
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    return Cipher(algorithms.AES(hashlib.md5(devicekey.encode()).digest()), modes.CBC(iv))


def encrypt(obj, devicekey, iv=None):
    iv = iv or os.urandom(16)
    plain = json.dumps(obj, separators=(",", ":")).encode()
    n = 16 - len(plain) % 16
    op = _cipher(devicekey, iv).encryptor()
    data = op.update(plain + bytes([n]) * n) + op.finalize()
    return base64.b64encode(data).decode(), base64.b64encode(iv).decode()


def decrypt(data, iv, devicekey):
    try:
        raw, iv = base64.b64decode(data), base64.b64decode(iv)
        op = _cipher(devicekey, iv).decryptor()
        plain = op.update(raw) + op.finalize()
    except (ValueError, TypeError):
        raise LocalError("Undecodable data") from None
    n = plain[-1] if plain else 0
    if not 1 <= n <= 16 or plain[-n:] != bytes([n]) * n:
        raise LocalError("Wrong key or bad padding")
    try:
        value = json.loads(plain[:-n].rstrip(b"\x02"))
    except ValueError:
        raise LocalError("Undecodable data") from None
    if not isinstance(value, dict):
        raise LocalError("Undecodable data")
    return value


# ---- mDNS ----------------------------------------------------------------

def mdns_query(name=SERVICE, qtype=T_PTR):
    """A standard one-question mDNS query (ID 0, no flags)."""
    q = b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\0"
    return struct.pack("!6H", 0, 0, 1, 0, 0, 0) + q + struct.pack("!2H", qtype, 1)


def _name(packet, pos, depth=0):
    labels = []
    while True:
        if pos >= len(packet) or depth > 20:
            raise ValueError("bad name")
        n = packet[pos]
        if n & 0xC0 == 0xC0:
            ptr = struct.unpack_from("!H", packet, pos)[0] & 0x3FFF
            labels.append(_name(packet, ptr, depth + 1)[0])
            return ".".join(x for x in labels if x), pos + 2
        if n == 0:
            return ".".join(labels), pos + 1
        labels.append(packet[pos + 1:pos + 1 + n].decode("utf-8", "replace"))
        pos += 1 + n


def parse_mdns(packet):
    """All resource records in a response: list of (name, type, value)."""
    if len(packet) < 12:
        return []
    _, flags, qd, an, ns, ar = struct.unpack_from("!6H", packet)
    if not flags & 0x8000:
        return []
    pos, out = 12, []
    try:
        for _ in range(qd):
            pos = _name(packet, pos)[1] + 4
        for _ in range(an + ns + ar):
            name, pos = _name(packet, pos)
            rtype, _, _, rdlen = struct.unpack_from("!2HIH", packet, pos)
            pos += 10
            rdata, end = packet[pos:pos + rdlen], pos + rdlen
            if rtype == T_A and rdlen == 4:
                out.append((name, rtype, socket.inet_ntoa(rdata)))
            elif rtype == T_PTR:
                out.append((name, rtype, _name(packet, pos)[0]))
            elif rtype == T_SRV and rdlen >= 7:
                out.append((name, rtype, (struct.unpack_from("!H", rdata, 4)[0], _name(packet, pos + 6)[0])))
            elif rtype == T_TXT:
                txt, i = {}, 0
                while i < len(rdata):
                    s = rdata[i + 1:i + 1 + rdata[i]].decode("utf-8", "replace")
                    i += 1 + rdata[i]
                    if "=" in s:
                        k, v = s.split("=", 1)
                        txt[k] = v
                out.append((name, rtype, txt))
            pos = end
    except (ValueError, struct.error, IndexError):
        pass  # Keep the records read before a malformed one.
    return out


def device_id(instance):
    """eWeLink_1000xxxxxx._ewelink._tcp.local -> 1000xxxxxx (None for other kit)."""
    head = instance.split(".", 1)[0]
    if not head.lower().startswith("ewelink_") or not instance.lower().endswith("." + SERVICE.lower()):
        return None
    return head[8:] or None


class Announcements:
    """Joins A, SRV and TXT records across packets into per-device updates."""

    def __init__(self):
        self.hosts, self.srv, self.last_seq = {}, {}, {}

    def feed(self, packet, source_ip=None):
        """Returns [(deviceid, ip, port, txt)] for each eWeLink TXT in the packet."""
        records = parse_mdns(packet)
        for name, rtype, value in records:
            if rtype == T_A:
                self.hosts[name.lower()] = value
            elif rtype == T_SRV:
                did = device_id(name)
                if did:
                    self.srv[did] = value
        out = []
        for name, rtype, value in records:
            did = device_id(name) if rtype == T_TXT else None
            if not did:
                continue
            port, target = self.srv.get(did, (None, None))
            ip = self.hosts.get((target or "").lower()) or source_ip
            out.append((did, ip, port or HTTP_PORT, value))
        return out


def txt_params(txt, devicekey):
    """Decrypted params from an announcement, or None if it carries none."""
    raw = "".join(txt.get("data%d" % i, "") for i in range(1, 5))
    if not raw:
        return None
    if txt.get("encrypt") == "true":
        return decrypt(raw, txt.get("iv", ""), devicekey)
    try:
        value = json.loads(raw)
    except ValueError:
        raise LocalError("Undecodable data") from None
    return value if isinstance(value, dict) else None


def mdns_socket(interface_ip="0.0.0.0"):
    """Shares port 5353 with avahi (both use SO_REUSEADDR)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except OSError:
            pass
    s.bind(("", MDNS_PORT))
    s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                 socket.inet_aton(MDNS_GROUP) + socket.inet_aton(interface_ip))
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
    return s


# ---- direct read on port 8081 ----------------------------------------------

def read_state(ip, port, deviceid, devicekey, command="info", timeout=4, opener=None):
    """Ask one plug for its state with an empty, encrypted request body. Read-only."""
    if command not in READ_COMMANDS:
        raise ValueError("Only state reads are permitted")
    data, iv = encrypt({}, devicekey)
    body = json.dumps({"sequence": str(int(time.time() * 1000)), "deviceid": deviceid,
                       "selfApikey": "123", "encrypt": True, "iv": iv, "data": data}).encode()
    req = urllib.request.Request("http://%s:%d/zeroconf/%s" % (ip, port, command), data=body,
                                 headers={"Content-Type": "application/json", "Connection": "close"},
                                 method="POST")
    try:
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as r:
            reply = json.loads(r.read(65537)[:65536])
    except (OSError, ValueError):
        raise LocalError("No reply") from None
    if not isinstance(reply, dict) or reply.get("error") != 0:
        raise LocalError("Refused (error %s)" % (reply.get("error") if isinstance(reply, dict) else "?"))
    if reply.get("iv") and reply.get("data"):
        return decrypt(reply["data"], reply["iv"], devicekey)
    return {}


# ---- collector ---------------------------------------------------------------

LOCAL_FRESH = 60       # seconds without a local answer before the cloud poll takes over
QUERY_SECONDS = 10     # how often to ask the plugs (by mDNS) to repeat their state
FLUSH_SECONDS = 5      # latest readings written at most this often
SAMPLE_SECONDS = 30    # one history sample per plug per this many seconds
# Only these models push a new state when their power changes. UIID 32 plugs (POW R2) answer
# with the state they last pushed, which can be hours old, so they stay on the cloud poll.
LIVE_UIIDS = {190}
EVENTS_SCHEMA = "CREATE TABLE IF NOT EXISTS local_events(ts INTEGER NOT NULL, device TEXT NOT NULL, kind TEXT NOT NULL);"


def load_keys(path):
    """Private file written from the cloud device list: {"devices": {slug: {id, devicekey, uiid}}}."""
    from ewelink import DEVICES, private_json
    devices = private_json(path).get("devices")
    if not isinstance(devices, dict):
        raise RuntimeError("Invalid local key file")
    out = {}
    for slug, d in devices.items():
        if (slug not in dict(DEVICES) or not isinstance(d, dict) or not isinstance(d.get("id"), str)
                or not d["id"] or not isinstance(d.get("devicekey"), str) or not d["devicekey"]):
            raise RuntimeError("Invalid local key file")
        out[slug] = d
    return out


class Plug:
    def __init__(self, slug, conf):
        self.slug, self.id, self.key, self.uiid = slug, conf["id"], conf["devicekey"], conf.get("uiid")
        self.heard = 0.0          # monotonic time of the last valid local answer
        self.values = None        # latest decoded metrics
        self.dirty = False
        self.lost = True          # starts on the cloud until it is heard
        self.window_start = None  # time-weighted power for the history sample
        self.energy = 0.0
        self.since = None


class EwelinkLocal:
    """Listens for the five power plugs on the LAN and writes their readings into
    the shared eWeLink history, marked source=local. Plugs not heard for a minute
    are left to the cloud poll (Ewelink.collect asks fresh() before writing)."""

    def __init__(self, ewelink, keys_path, sock=None, clock=time.monotonic, wall=time.time):
        from ewelink import metrics
        self.ewelink, self.keys_path, self.metrics = ewelink, keys_path, metrics
        self.sock, self.clock, self.wall = sock, clock, wall
        self.plugs = {}
        self.by_id = {}
        self.ann = Announcements()
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.last_flush = 0.0
        self.events = []

    def load(self):
        keys = load_keys(self.keys_path)
        self.plugs = {slug: Plug(slug, conf) for slug, conf in keys.items() if conf.get("uiid") in LIVE_UIIDS}
        self.by_id = {p.id: p for p in self.plugs.values()}

    def fresh(self, slug):
        with self.lock:
            p = self.plugs.get(slug)
            return bool(p and p.heard and self.clock() - p.heard < LOCAL_FRESH)

    def handle(self, packet, source_ip=None):
        """Decode one mDNS packet. Returns the slugs that were updated."""
        updated = []
        for did, _, _, txt in self.ann.feed(packet, source_ip):
            p = self.by_id.get(did)
            if not p:
                continue
            try:
                params = txt_params(txt, p.key)
            except LocalError:
                continue
            values = self.metrics(params or {}, p.uiid)
            if "power_w" not in values:
                continue
            now = self.clock()
            with self.lock:
                self._accumulate(p, now)
                p.values, p.heard, p.dirty = values, now, True
                if p.lost:
                    p.lost = False
                    self._event(p, "local_back")
            updated.append(p.slug)
        return updated

    def _accumulate(self, p, now):
        if p.values is not None and p.since is not None and now - p.since < LOCAL_FRESH:
            p.energy += p.values["power_w"] * (now - p.since)
        if p.window_start is None:
            p.window_start = now
        p.since = now

    def _event(self, p, kind):
        LOG.info("ewelink-local: %s %s", p.slug, kind)
        self.events.append((int(self.wall() * 1000), p.slug, kind))

    def tick(self):
        """Mark lost plugs, then write latest readings and due history samples."""
        now = self.clock()
        rows_latest, rows_samples = [], []
        with self.lock:
            for p in self.plugs.values():
                if not p.lost and p.heard and now - p.heard >= LOCAL_FRESH:
                    p.lost = True
                    p.values, p.window_start, p.energy, p.since = None, None, 0.0, None
                    self._event(p, "local_lost")
                if p.lost or p.values is None:
                    continue
                payload = dict(p.values, online=True, source="local")
                if p.dirty:
                    rows_latest.append((p.slug, payload))
                    p.dirty = False
                if p.window_start is not None and now - p.window_start >= SAMPLE_SECONDS:
                    self._accumulate(p, now)
                    span = now - p.window_start
                    sample = dict(payload)
                    if span > 0:
                        sample["power_w"] = round(p.energy / span, 3)
                    rows_samples.append((p.slug, sample))
                    rows_latest.append((p.slug, payload))
                    p.window_start, p.energy = now, 0.0
            events, self.events = self.events, []
        if rows_latest or rows_samples or events:
            self.write(rows_latest, rows_samples, events)

    def write(self, rows_latest, rows_samples, events):
        ms = int(self.wall() * 1000)
        with self.ewelink.database() as db, db:
            db.executescript(EVENTS_SCHEMA)
            for slug, value in rows_samples:
                db.execute("INSERT OR REPLACE INTO samples VALUES(?,?,?)",
                           (slug, ms, json.dumps(value, separators=(",", ":"), allow_nan=False)))
            for slug, value in rows_latest:
                db.execute("INSERT OR REPLACE INTO latest VALUES(?,?,?)",
                           (slug, ms, json.dumps(value, separators=(",", ":"), allow_nan=False)))
            db.executemany("INSERT INTO local_events VALUES(?,?,?)", events)
            db.execute("DELETE FROM local_events WHERE ts<?", (ms - 90 * 86400000,))

    def query(self):
        try:
            self.sock.sendto(mdns_query(), (MDNS_GROUP, MDNS_PORT))
        except OSError:
            pass

    def poll(self):
        """Worker loop (run.py starts it in a thread)."""
        try:
            self.load()
        except (OSError, RuntimeError, ValueError):
            LOG.warning("ewelink-local: key file missing or invalid; cloud only")
            return
        next_query = 0.0
        while not self.stop.is_set():
            try:
                if self.sock is None:
                    self.sock = mdns_socket()
                    self.sock.settimeout(1)
                now = self.clock()
                if now >= next_query:
                    self.query()
                    next_query = now + QUERY_SECONDS
                try:
                    packet, (src, _) = self.sock.recvfrom(9000)
                    self.handle(packet, src)
                except socket.timeout:
                    pass
                if self.clock() - self.last_flush >= FLUSH_SECONDS:
                    self.last_flush = self.clock()
                    self.tick()
            except (OSError, RuntimeError, ValueError, sqlite3.Error):
                LOG.warning("ewelink-local: listener error; retrying")
                with contextlib.suppress(OSError, AttributeError):
                    self.sock.close()
                self.sock = None
                self.stop.wait(10)
        with contextlib.suppress(OSError, AttributeError):
            self.sock.close()
