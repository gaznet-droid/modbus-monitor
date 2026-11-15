#!/usr/bin/env python3
"""Live power for the three POWR2 plugs (Garden, 600 watt, Charger) over eWeLink's cloud WebSocket.

The POWR2 plugs (UIID 32) do not stream power on the LAN and their cloud figure sits still for
hours. The eWeLink app makes them stream by sending uiActive whenever someone opens a plug's page;
this module does the same, approved by Gareth on 15 Nov 2026. It keeps one WebSocket open, sends
{"uiActive": 60} to each of the three plugs about once a minute, and stores the power they push
back into the shared eWeLink history, marked source=live.

uiActive is the ONLY parameter this module can send. It does not switch a plug or change a setting;
send() refuses anything else before it reaches the socket. Standard library only.

API use: one budgeted device call when the worker starts (for the plugs' API key and model), and
the WebSocket address (dispatch) once per connection, at most MAX_CONNECTS a day. WebSocket
messages are not REST calls. The cloud poll in ewelink.py stays as the fallback for any plug not
heard here for FRESH seconds.
Off switch: "ewelink_live_enabled": false in /etc/modpi/monitor.json, then restart modpi.
"""
import base64
import contextlib
import hashlib
import json
import logging
import os
import re
import socket
import sqlite3
import ssl
import struct
import threading
import time
import urllib.error
import urllib.request

LOG = logging.getLogger("ewelink-live")

LIVE_SLUGS = ("garage-inverter", "inverter-600", "charger-inverter")
LIVE_UIIDS = {32}            # POWR2 only; the POWR316 plugs are read on the LAN
UI_ACTIVE = 60               # seconds a plug streams after each ask (the app's own value)
RENEW = 50                   # ask again before the last ask runs out
ASK_GAP = 1.05               # at least a second between asks (SonoffLAN issue 1366)
FRESH = 120                  # seconds without a push before the cloud poll takes a plug back
SAMPLE_SECONDS = 30
FLUSH_SECONDS = 3
SILENT = 300                 # reconnect when nothing at all has arrived for this long
MAX_CONNECTS = 48            # WebSocket address lookups in any 24 hours
DISPATCH = {"eu": "https://eu-dispa.coolkit.cc/dispatch/app",
            "us": "https://us-dispa.coolkit.cc/dispatch/app",
            "as": "https://as-dispa.coolkit.cc/dispatch/app",
            "cn": "https://cn-dispa.coolkit.cn/dispatch/app"}
WS_HOST = re.compile(r"[a-z0-9.-]{1,100}\.coolkit\.(cc|cn)")
GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_FRAME = 1 << 20


class LiveError(RuntimeError):
    """A failed live session. The message never holds a token, key or device ID."""


# ---- minimal WebSocket client (RFC 6455, client side) ------------------------------------

class WebSocket:
    def __init__(self, sock):
        self.sock, self.buf, self.parts = sock, b"", []

    @classmethod
    def connect(cls, host, port, path="/api/ws", timeout=10):
        raw = socket.create_connection((host, port), timeout=timeout)
        try:
            sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
        except (OSError, ssl.SSLError):
            raw.close()
            raise
        ws = cls(sock)
        try:
            ws.handshake(host, path)
        except Exception:
            sock.close()
            raise
        return ws

    def handshake(self, host, path):
        key = base64.b64encode(os.urandom(16))
        self.sock.sendall(("GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n"
                           % (path, host, key.decode())).encode())
        while b"\r\n\r\n" not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk or len(self.buf) > 16384:
                raise LiveError("WebSocket handshake failed")
            self.buf += chunk
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        lines = head.decode("latin-1").split("\r\n")
        if " 101 " not in lines[0] + " ":
            raise LiveError("WebSocket refused")
        accept = base64.b64encode(hashlib.sha1(key + GUID).digest()).decode()
        headers = {k.strip().lower(): v.strip() for k, v in (l.split(":", 1) for l in lines[1:] if ":" in l)}
        if headers.get("sec-websocket-accept") != accept:
            raise LiveError("WebSocket handshake failed")

    def _frame(self, opcode, payload):
        head = bytes([0x80 | opcode])
        n = len(payload)
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        return head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

    def send_text(self, text):
        self.sock.sendall(self._frame(0x1, text.encode()))

    def _take(self):
        """One complete frame from the buffer, or None."""
        b = self.buf
        if len(b) < 2:
            return None
        fin, opcode, masked, n, pos = b[0] & 0x80, b[0] & 0x0F, b[1] & 0x80, b[1] & 0x7F, 2
        if n == 126:
            if len(b) < 4:
                return None
            n, pos = struct.unpack(">H", b[2:4])[0], 4
        elif n == 127:
            if len(b) < 10:
                return None
            n, pos = struct.unpack(">Q", b[2:10])[0], 10
        if n > MAX_FRAME:
            raise LiveError("WebSocket frame too large")
        mask = b""
        if masked:
            mask, pos = b[pos:pos + 4], pos + 4
        if len(b) < pos + n:
            return None
        payload = b[pos:pos + n]
        if masked:
            payload = bytes(x ^ mask[i % 4] for i, x in enumerate(payload))
        self.buf = b[pos + n:]
        return fin, opcode, payload

    def recv(self, timeout=1.0):
        """The next text message, or None after timeout. Answers pings; raises on close."""
        deadline = time.monotonic() + timeout
        while True:
            frame = self._take()
            if frame:
                fin, opcode, payload = frame
                if opcode == 0x9:
                    self.sock.sendall(self._frame(0xA, payload))
                elif opcode == 0x8:
                    raise LiveError("WebSocket closed by server")
                elif opcode in (0x1, 0x0):
                    self.parts.append(payload)
                    if sum(map(len, self.parts)) > MAX_FRAME:
                        raise LiveError("WebSocket message too large")
                    if fin:
                        text, self.parts = b"".join(self.parts), []
                        return text.decode("utf-8", "replace")
                continue
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            self.sock.settimeout(left)
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                return None
            if not chunk:
                raise LiveError("WebSocket connection dropped")
            self.buf += chunk

    def close(self):
        with contextlib.suppress(OSError):
            self.sock.sendall(self._frame(0x8, b""))
        with contextlib.suppress(OSError):
            self.sock.close()


# ---- the only messages this module can send ------------------------------------------------

def ui_active_message(apikey, deviceid, sequence):
    return {"action": "update", "apikey": apikey, "deviceid": deviceid,
            "params": {"uiActive": UI_ACTIVE}, "userAgent": "app", "sequence": sequence}


def check_outgoing(message):
    """Refuse anything but the sign-in and a uiActive ask, before it can be sent."""
    if not isinstance(message, dict):
        raise ValueError("Refused outgoing message")
    if message.get("action") == "userOnline":
        if "params" in message:
            raise ValueError("Refused outgoing message")
        return
    if (message.get("action") != "update" or message.get("params") != {"uiActive": UI_ACTIVE}
            or set(message) != {"action", "apikey", "deviceid", "params", "userAgent", "sequence"}):
        raise ValueError("Refused outgoing message")


class Plug:
    def __init__(self, slug, deviceid, apikey):
        self.slug, self.id, self.apikey = slug, deviceid, apikey
        self.heard = 0.0
        self.values = None
        self.dirty = False
        self.lost = True
        self.window_start = None
        self.energy = 0.0
        self.since = None


class EwelinkLive:
    def __init__(self, ewelink, connect=None, dispatcher=None, clock=time.monotonic, wall=time.time):
        from ewelink import metrics
        self.ewelink, self.metrics = ewelink, metrics
        self.connect_ws = connect or WebSocket.connect
        self.dispatcher = dispatcher or self._dispatch
        self.clock, self.wall = clock, wall
        self.plugs, self.by_id = {}, {}
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.events = []
        self.connects = []
        self.last_flush = 0.0
        self.sequence_last = 0
        self.status = "Starting"

    # -- setup -----------------------------------------------------------------------------
    def setup(self):
        """One budgeted device call: which of the three plugs are POWR2, and their API key."""
        with self.ewelink.exclusive():
            config = self.ewelink.config()
            mapping = config.get("devices", {})
            wanted = {}
            for slug in LIVE_SLUGS:
                value = mapping.get(slug)
                if value is None:
                    continue
                entry = value if isinstance(value, dict) else {"id": value, "item_type": 1}
                if isinstance(entry.get("id"), str) and entry["id"] and entry.get("item_type", 1) in (1, 2):
                    wanted[entry["id"]] = (slug, entry.get("item_type", 1))
            if not wanted:
                raise LiveError("No live plugs configured")
            t = self.ewelink.token()
            result = self.ewelink.request(t["region"], "POST", "/v2/device/thing",
                {"thingList": [{"itemType": it, "id": i} for i, (_, it) in wanted.items()]}, t["accessToken"])
        plugs = {}
        for x in result.get("thingList", []):
            d = x.get("itemData") if isinstance(x, dict) else None
            if not isinstance(d, dict) or d.get("deviceid") not in wanted:
                continue
            if d.get("extra", {}).get("uiid") not in LIVE_UIIDS or not isinstance(d.get("apikey"), str) or not d["apikey"]:
                continue
            slug = wanted[d["deviceid"]][0]
            plugs[slug] = Plug(slug, d["deviceid"], d["apikey"])
        if not plugs:
            raise LiveError("No POWR2 plugs found")
        with self.lock:
            self.plugs, self.by_id = plugs, {p.id: p for p in plugs.values()}
        LOG.info("ewelink-live: %d plug(s) to keep live", len(plugs))

    def _dispatch(self, region, token):
        if region not in DISPATCH:
            raise LiveError("Unknown region")
        request = urllib.request.Request(DISPATCH[region], headers={"Authorization": "Bearer " + token})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                data = json.loads(response.read(65537)[:65536])
        except urllib.error.HTTPError:
            raise LiveError("WebSocket address lookup refused") from None
        host, port = data.get("domain"), data.get("port")
        if not isinstance(host, str) or not WS_HOST.fullmatch(host) or type(port) is not int or not 1 <= port <= 65535:
            raise LiveError("Unexpected WebSocket address")
        return host, port

    def sequence(self):
        self.sequence_last = max(self.sequence_last + 1, int(self.wall() * 1000))
        return str(self.sequence_last)

    def send(self, ws, message):
        check_outgoing(message)
        ws.send_text(json.dumps(message, separators=(",", ":")))

    def connect_allowed(self):
        now = self.wall()
        self.connects = [t for t in self.connects if now - t < 86400]
        return len(self.connects) < MAX_CONNECTS

    # -- one session -------------------------------------------------------------------------
    def session(self):
        with self.ewelink.exclusive():
            config, t = self.ewelink.config(), self.ewelink.token()
        apikey = next(iter(self.plugs.values())).apikey
        self.connects.append(self.wall())
        host, port = self.dispatcher(t["region"], t["accessToken"])
        ws = self.connect_ws(host, port)
        try:
            ts = self.wall()
            self.send(ws, {"action": "userOnline", "at": t["accessToken"], "apikey": apikey,
                           "appid": config["appid"], "nonce": str(int(ts / 100)), "ts": int(ts),
                           "userAgent": "app", "sequence": self.sequence(), "version": 8})
            heartbeat, deadline = 0, self.clock() + 15
            while True:
                text = ws.recv(1.0)
                if self.stop.is_set():
                    return
                if text is None:
                    if self.clock() > deadline:
                        raise LiveError("No sign-in reply")
                    continue
                reply = self._json(text)
                if reply is not None and "action" not in reply and ("config" in reply or "error" in reply):
                    if reply.get("error", 0) != 0:
                        raise LiveError("Sign-in refused (%s)" % reply.get("error"))
                    hb = reply.get("config", {}) if isinstance(reply.get("config"), dict) else {}
                    if hb.get("hb") and type(hb.get("hbInterval")) is int and 10 <= hb["hbInterval"] <= 600:
                        heartbeat = hb["hbInterval"]
                    break
            self._event(None, "live_connected")
            self.status = "Connected"
            next_ask, next_ping, last_any = 0.0, self.clock() + (heartbeat or 90), self.clock()
            while not self.stop.is_set():
                now = self.clock()
                if now >= next_ask:
                    for p in list(self.plugs.values()):
                        self.send(ws, ui_active_message(p.apikey, p.id, self.sequence()))
                        if self.stop.wait(ASK_GAP):
                            return
                    next_ask = now + RENEW
                if now >= next_ping:
                    ws.send_text("ping")
                    next_ping = now + (heartbeat or 90)
                text = ws.recv(1.0)
                if text is not None:
                    last_any = self.clock()
                    self.handle(text)
                elif self.clock() - last_any > SILENT:
                    raise LiveError("WebSocket silent")
                if self.clock() - self.last_flush >= FLUSH_SECONDS:
                    self.last_flush = self.clock()
                    self.tick()
        finally:
            ws.close()

    @staticmethod
    def _json(text):
        try:
            value = json.loads(text)
        except ValueError:
            return None   # "pong" and other plain text
        return value if isinstance(value, dict) else None

    # -- readings ----------------------------------------------------------------------------
    def handle(self, text):
        """One message from the server. Returns the slug updated, if any."""
        msg = self._json(text)
        if not msg or msg.get("action") != "update" or not isinstance(msg.get("params"), dict):
            return None
        p = self.by_id.get(msg.get("deviceid"))
        if not p:
            return None
        values = self.metrics(msg["params"], 32)
        if values.get("switch") == "off":
            values.update(power_w=0.0, current_a=0.0)   # off means nothing flows, whatever was last pushed
        if not values:
            return None   # e.g. the echo of our own uiActive
        now = self.clock()
        with self.lock:
            merged = dict(p.values or {}, **values)
            if "power_w" not in merged:
                return None
            self._accumulate(p, now)
            p.values, p.dirty = merged, True
            if "power_w" in values:
                p.heard = now
                if p.lost:
                    p.lost = False
                    self._event(p, "live_on")
        return p.slug

    def fresh(self, slug):
        with self.lock:
            p = self.plugs.get(slug)
            return bool(p and not p.lost and p.heard and self.clock() - p.heard < FRESH)

    def _accumulate(self, p, now):
        if p.values is not None and p.since is not None and now - p.since < FRESH:
            p.energy += p.values["power_w"] * (now - p.since)
        if p.window_start is None:
            p.window_start = now
        p.since = now

    def _event(self, p, kind):
        LOG.info("ewelink-live: %s %s", p.slug if p else "-", kind)
        self.events.append((int(self.wall() * 1000), p.slug if p else "-", kind))

    def tick(self):
        now = self.clock()
        rows_latest, rows_samples = [], []
        with self.lock:
            for p in self.plugs.values():
                if not p.lost and p.heard and now - p.heard >= FRESH:
                    p.lost = True
                    p.values, p.window_start, p.energy, p.since = None, None, 0.0, None
                    self._event(p, "live_lost")
                if p.lost or p.values is None:
                    continue
                payload = dict(p.values, online=True, source="live")
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
                    p.window_start, p.energy = now, 0.0
            events, self.events = self.events, []
        if rows_latest or rows_samples or events:
            self.write(rows_latest, rows_samples, events)

    def write(self, rows_latest, rows_samples, events):
        from ewelink_local import EVENTS_SCHEMA
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
        with self.ewelink.read_lock:
            self.ewelink.history_cache.clear()

    # -- worker loop -------------------------------------------------------------------------
    def poll(self):
        """Worker loop (run.py starts it in a thread). Never stops modpi; errors back off."""
        backoff, next_setup = 30, 0.0
        while not self.stop.is_set():
            try:
                if not self.plugs:
                    # The device call counts against the 400-a-day budget: at most once an hour.
                    wait = next_setup - self.clock()
                    if wait > 0:
                        self.stop.wait(wait)
                        continue
                    next_setup = self.clock() + 3600
                    try:
                        self.setup()
                    except LiveError as error:
                        # No POWR2 plugs (for example Dad swapped them): nothing to do until a restart.
                        LOG.warning("ewelink-live: %s; stopped", error)
                        self.status = "Not needed"
                        return
                if not self.connect_allowed():
                    self.status = "Paused: too many reconnects today"
                    self.stop.wait(900)
                    continue
                self.session()
                backoff = 30
            except Exception as error:  # a bad session must never take modpi down
                self.status = "Reconnecting"
                LOG.warning("ewelink-live: %s: %s; retrying in %ds", type(error).__name__,
                            str(error)[:80] if isinstance(error, (LiveError, ValueError)) else "", backoff)
                self.events.append((int(self.wall() * 1000), "-", "live_error"))
                with contextlib.suppress(Exception):
                    self.tick()
                if self.stop.wait(backoff):
                    break
                backoff = min(backoff * 2, 900)
            with contextlib.suppress(Exception):
                self.tick()
