#!/usr/bin/env python3
"""Read-only Modbus power monitor. Python standard library only (passkey sign-in also uses python3-cryptography)."""
import argparse
import contextlib
import hashlib
import hmac
import http.client
import json
import logging
import math
import os
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import signal
import socket
import sqlite3
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from auth import AuthError

LOG = logging.getLogger("modbus-monitor")
WINDOWS = {"15m": 900, "30m": 1800, "1h": 3600, "2h": 7200,
           "4h": 14400, "8h": 28800, "1d": 86400, "24h": 86400, "7d": 604800, "30d": 2592000,
           "90d": 7776000, "365d": 31536000}
with (Path(__file__).parent/"tz/Europe-London.tzif").open("rb") as timezone_file:
    LONDON=ZoneInfo.from_file(timezone_file,key="Europe/London")

# Written by pi/autodeploy.sh after each deploy; absent on non-Pi hosts.
VERSION_FILE=Path(__file__).resolve().parents[1]/"VERSION.json"
# GitHub push webhook: a valid signed push to main touches the request file, which
# modpi-deploy.path watches. It can only make the Pi pull from GitHub, nothing else.
DEPLOY_SECRET_FILE=Path("/var/lib/modpi/private/deploy-hook.secret")
DEPLOY_REQUEST_FILE=Path("/var/lib/modpi/deploy-request")
DEPLOY_BRANCH_REF="refs/heads/main"
SHUTDOWN_REQUEST_FILE=Path("/var/lib/modpi/shutdown-request")  # modpi-shutdown.path powers off on change

def period_bounds(window, now):
    if window=="1d":
        midnight=datetime.fromtimestamp(now/1000,LONDON).replace(hour=0,minute=0,second=0,microsecond=0)
        return int(midnight.timestamp()*1000),int((midnight+timedelta(days=1)).timestamp()*1000)
    return now-WINDOWS[window]*1000,now
SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
 ts INTEGER PRIMARY KEY, value REAL NOT NULL);
CREATE TABLE IF NOT EXISTS rollups (
 period INTEGER NOT NULL, bucket INTEGER NOT NULL, n INTEGER NOT NULL,
 total REAL NOT NULL, low REAL NOT NULL, high REAL NOT NULL,
 import_wh REAL NOT NULL, export_wh REAL NOT NULL, coverage_s REAL NOT NULL,
 PRIMARY KEY(period,bucket));
CREATE TABLE IF NOT EXISTS state (
 id INTEGER PRIMARY KEY CHECK(id=1), ts INTEGER NOT NULL, value REAL NOT NULL,
 import_wh REAL NOT NULL, export_wh REAL NOT NULL, coverage_s REAL NOT NULL);
CREATE TABLE IF NOT EXISTS meter_state (
 id INTEGER PRIMARY KEY CHECK(id=1), ts INTEGER NOT NULL, kwh REAL NOT NULL);
PRAGMA user_version=2;
"""

def receive_exact(sock, count):
    data = bytearray()
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise OSError("Incomplete Modbus response")
        data.extend(chunk)
    return bytes(data)

def read_input_float(host, port=8838, unit=1, register=146, function=4):
    """Read two registers using function 03 or 04. Never write to the meter."""
    if function not in (3,4):
        raise ValueError("Only read functions are allowed")
    transaction = int.from_bytes(os.urandom(2), "big")
    request = struct.pack(">HHHBBHH", transaction, 0, 6, unit, function, register, 2)
    with socket.create_connection((host, port), timeout=3) as sock:
        sock.settimeout(3)
        sock.sendall(request)
        tid, protocol, length, received_unit = struct.unpack(">HHHB", receive_exact(sock, 7))
        if tid != transaction or protocol != 0 or received_unit != unit or length != 7:
            raise ValueError("Invalid Modbus response header")
        payload = receive_exact(sock, length - 1)
        if payload[:2] != bytes((function,4)):
            raise ValueError("Invalid Modbus register response")
        value = struct.unpack(">f", payload[2:])[0]
        if not math.isfinite(value):
            raise ValueError("Non-finite power reading")
        return value

def read_power(host, port=8838, unit=1, register=146):
    return round(read_input_float(host,port,unit,register),2)

def read_combined_kwh(host, port=8838):
    # Register and big-endian float order verified against the live meter.
    value=read_input_float(host,port,unit=1,register=1792)
    if value<0:
        raise ValueError("Negative combined meter reading")
    return value

class VoltageReader:
    """No worker, timer or persistence: read only when an HTTP client asks.

    A shared three-second cache/lock bounds meter traffic from multiple tabs,
    including failed requests. Construction and ordinary power requests do no IO.
    """
    def __init__(self, host, port=8838):
        self.host,self.port=host,port
        self.lock=threading.Lock()
        self.attempt=None
        self.reading=None

    def sample(self):
        with self.lock:
            if self.attempt is None or time.monotonic()-self.attempt>=3:
                self.reading=None
                try:
                    # Exact request from Dad's log: unit 1, FC03, address 16, 2 words.
                    value=read_input_float(self.host,self.port,unit=1,register=16,function=3)
                    if not 0<=value<=1000:
                        raise ValueError("Invalid voltage")
                    self.reading={"ts":int(time.time()*1000),"volts":round(value,2)}
                except (OSError,ValueError):
                    pass
                finally:
                    self.attempt=time.monotonic()
            if self.reading is None:
                raise OSError("Voltage unavailable")
            return dict(self.reading)

def split_energy(previous, current, seconds):
    """Trapezoid integration, splitting at a zero crossing; never fill long gaps."""
    if not 0 < seconds <= 10:
        return 0., 0., 0.
    if previous * current >= 0:
        energy = (previous + current) * .5 * seconds / 3600
        return max(energy, 0), max(-energy, 0), seconds
    fraction = abs(previous) / (abs(previous) + abs(current))
    first = previous * .5 * seconds * fraction / 3600
    second = current * .5 * seconds * (1 - fraction) / 3600
    return max(first, 0) + max(second, 0), max(-first, 0) + max(-second, 0), seconds

class Store:
    def __init__(self, path, guard=lambda: None):
        self.guard = guard
        self.guard()
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=3)
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA cache_size=-512")
        self.db.executescript(SCHEMA)
        self.last_cleanup = 0

    def add(self, timestamp, value):
        self.guard()
        with self.lock, self.db:
            previous = self.db.execute("SELECT ts,value,import_wh,export_wh,coverage_s FROM state WHERE id=1").fetchone()
            if previous and timestamp <= previous[0]:
                return False
            imp, exp, coverage = split_energy(previous[1], value, (timestamp-previous[0])/1000) if previous else (0., 0., 0.)
            self.db.execute("INSERT INTO samples VALUES (?,?)", (timestamp, value))
            for period in (60, 3600):
                bucket = timestamp // (period*1000) * (period*1000)
                self.db.execute("""INSERT INTO rollups VALUES (?,?,1,?,?,?,?,?,?)
                  ON CONFLICT(period,bucket) DO UPDATE SET n=n+1,total=total+excluded.total,
                  low=min(low,excluded.low),high=max(high,excluded.high),
                  import_wh=import_wh+excluded.import_wh,export_wh=export_wh+excluded.export_wh,
                  coverage_s=coverage_s+excluded.coverage_s""",
                  (period,bucket,value,value,value,imp,exp,coverage))
            totals = (previous[2]+imp,previous[3]+exp,previous[4]+coverage) if previous else (0.,0.,0.)
            self.db.execute("INSERT OR REPLACE INTO state VALUES (1,?,?,?,?,?)", (timestamp,value,*totals))
            if timestamp-self.last_cleanup > 3600000:
                # Bounded batches avoid a long lock after extended downtime.
                self.db.execute("DELETE FROM samples WHERE ts IN (SELECT ts FROM samples WHERE ts<? LIMIT 5000)", (timestamp-7*86400000,))
                self.db.execute("DELETE FROM rollups WHERE period=60 AND bucket IN (SELECT bucket FROM rollups WHERE period=60 AND bucket<? LIMIT 5000)", (timestamp-365*86400000,))
                self.last_cleanup = timestamp
            return True

    def save_meter(self, timestamp, kwh):
        self.guard()
        if not math.isfinite(kwh) or kwh<0:
            raise ValueError("Invalid combined meter reading")
        with self.lock,self.db:
            self.db.execute("INSERT OR REPLACE INTO meter_state VALUES (1,?,?)",(timestamp,kwh))

    def live(self, since=0, now=None, window=None):
        self.guard()
        now = int(time.time()*1000) if now is None else now
        with self.lock:
            row = self.db.execute("SELECT ts,value,import_wh,export_wh,coverage_s FROM state WHERE id=1").fetchone()
            recent = self.db.execute("SELECT ts,value FROM samples WHERE ts>? ORDER BY ts DESC LIMIT 1001", (since,)).fetchall() if since else []
            meter=self.db.execute("SELECT ts,kwh FROM meter_state WHERE id=1").fetchone()
        latest = dict(zip(("ts","value","import_wh","export_wh","coverage_s"),row)) if row else None
        result = {"latest":latest,"stale":not row or now-row[0]>12000,
                "server_time":now,"points":list(reversed(recent[:1000])),
                "reload":len(recent)>1000}
        result["meter"]={"ts":meter[0],"kwh":meter[1],"stale":now-meter[0]>90000} if meter else None
        if window:
            start,end=period_bounds(window,now)
            result["energy"] = self.energy(start,min(end,now))
            result["period"]={"start":start,"end":end}
        return result

    def energy(self, start, end):
        """Sum stored interval energy efficiently, clipping recent boundary intervals.

        Rollups attribute energy to the sample ending an interval. Raw boundary
        samples allow us to remove/add the fractional interval at either end.
        Once raw data expires, use complete minute groups, without inventing
        power readings for the partial groups.
        """
        self.guard()
        with self.lock:
            first = self.db.execute("SELECT min(ts) FROM samples").fetchone()[0]
            resolution = 1
            oldest = self.db.execute("SELECT min(bucket) FROM rollups WHERE period=60").fetchone()[0]
            raw_expired = first is not None and oldest is not None and first-oldest>=60000
            start_energy = start
            if raw_expired and start<first:
                start_energy=(max(start,oldest)+59999)//60000*60000
                resolution=60
            end_energy = end
            if raw_expired and end < first:
                end_energy = end//60000*60000
                resolution = 60
            if end_energy <= start_energy:
                return {"import_wh":0.,"export_wh":0.,"coverage_s":0.,"resolution_s":resolution}

            def buckets(period, lo, hi):
                return self.db.execute("SELECT coalesce(sum(import_wh),0),coalesce(sum(export_wh),0),coalesce(sum(coverage_s),0) FROM rollups WHERE period=? AND bucket>=? AND bucket<?", (period,lo,hi)).fetchone()

            def raw(lo, hi):
                total = [0.,0.,0.]
                previous = self.db.execute("SELECT ts,value FROM samples WHERE ts<? ORDER BY ts DESC LIMIT 1",(lo,)).fetchone()
                for ts,value in self.db.execute("SELECT ts,value FROM samples WHERE ts>=? AND ts<? ORDER BY ts",(lo,hi)):
                    if previous:
                        values=split_energy(previous[1],value,(ts-previous[0])/1000)
                        total=[a+b for a,b in zip(total,values)]
                    previous=(ts,value)
                return total

            def sum_minutes(lo, hi):
                a=(lo+59999)//60000*60000
                b=hi//60000*60000
                if a>b:
                    return raw(lo,hi)
                return [x+y+z for x,y,z in zip(raw(lo,a),buckets(60,a,b),raw(b,hi))]

            a=(start_energy+3599999)//3600000*3600000
            b=end_energy//3600000*3600000
            if a>b:
                total=sum_minutes(start_energy,end_energy)
            else:
                total=[x+y+z for x,y,z in zip(sum_minutes(start_energy,a),buckets(3600,a,b),sum_minutes(b,end_energy))]

            def before_boundary(timestamp):
                previous=self.db.execute("SELECT ts,value FROM samples WHERE ts<? ORDER BY ts DESC LIMIT 1",(timestamp,)).fetchone()
                following=self.db.execute("SELECT ts,value FROM samples WHERE ts>=? ORDER BY ts LIMIT 1",(timestamp,)).fetchone()
                if not previous or not following or not 0<following[0]-previous[0]<=10000:
                    return (0.,0.,0.)
                seconds=(timestamp-previous[0])/1000
                value=previous[1]+(following[1]-previous[1])*(timestamp-previous[0])/(following[0]-previous[0])
                return split_energy(previous[1],value,seconds)

            left=before_boundary(start_energy)
            right=before_boundary(end_energy)
            total=[max(0.,x-y+z) for x,y,z in zip(total,left,right)]
        return dict(zip(("import_wh","export_wh","coverage_s"),total),resolution_s=resolution)

    def history(self, window="15m", now=None, start=None, end=None):
        self.guard()
        now = int(time.time()*1000) if now is None else now
        custom = start is not None or end is not None
        if custom:
            if start is None or end is None or not 0<=start<end<=now or end-start>365*86400000:
                raise ValueError("Invalid date range")
        else:
            start,end=period_bounds(window,now)
        seconds = (end-start)/1000
        period = 0 if seconds<=14400 and start>=now-7*86400000 else 60 if seconds<=604800 else 3600
        steps = (3,6,15,30,60,120,300,600,900,1800,3600,7200,10800,21600,43200,86400)
        step = next(s for s in steps if s>=max(period or 3,seconds/1000))
        with self.lock:
            if not period:
                points = self.db.execute("SELECT CAST(ts/? AS INTEGER)*?,avg(value),min(value),max(value),count(*) FROM samples WHERE ts>=? AND ts<=? GROUP BY 1 ORDER BY 1", (step*1000,step*1000,start,end)).fetchall()
            else:
                points = self.db.execute("SELECT CAST(bucket/? AS INTEGER)*?,sum(total)/sum(n),min(low),max(high),sum(n) FROM rollups WHERE period=? AND bucket>=? AND bucket<=? GROUP BY 1 ORDER BY 1", (step*1000,step*1000,period,start,end)).fetchall()
            summary=self.energy(start,min(end,now))
            live=self.live(now=now)
        return {"window":"custom" if custom else window,"start":start,"end":end,"step_ms":step*1000,
                "points":points,"live":live,"energy":summary}

class Monitor:
    def __init__(self, store, host, port=8838):
        self.store, self.host, self.port = store, host, port
        self.stop = threading.Event()
        self.failed = False
        self.meter_failed = False
        self.next_meter = 0

    def poll(self):
        while not self.stop.is_set():
            started = time.monotonic()
            try:
                self.store.guard()
                value = read_power(self.host,self.port)
                self.store.add(int(time.time()*1000),value)
                if self.failed:
                    LOG.info("Meter collection recovered")
                self.failed = False
            except (OSError,ValueError,RuntimeError,sqlite3.Error):
                if not self.failed:
                    LOG.warning("Collection unavailable; retrying without filling missing readings")
                self.failed = True
            if time.monotonic()>=self.next_meter:
                self.next_meter=time.monotonic()+60
                try:
                    self.store.guard()
                    kwh=read_combined_kwh(self.host,self.port)
                    self.store.save_meter(int(time.time()*1000),kwh)
                    if self.meter_failed:
                        LOG.info("Combined energy reading recovered")
                    self.meter_failed=False
                except (OSError,ValueError,RuntimeError,sqlite3.Error):
                    if not self.meter_failed:
                        LOG.warning("Combined energy reading unavailable; keeping last successful value")
                    self.meter_failed=True
            self.stop.wait(max(.2,3-(time.monotonic()-started)))

ICON_FILES={
    "/favicon.svg":"image/svg+xml",
    "/favicon.ico":"image/x-icon",
    "/apple-touch-icon.png":"image/png",
    "/icon-192.png":"image/png",
    "/icon-512.png":"image/png",
    "/icon-maskable-512.png":"image/png",
    "/site.webmanifest":"application/manifest+json",
}

# Reachable without signing in when login is on: sign-in itself, health and deploy checks, the eWeLink
# OAuth callback, and static files that are public in the repo anyway. Everything else needs a session.
AUTH_PUBLIC={"/login","/enrol","/auth.js","/health","/version","/api/integrations/ewelink/callback",
    "/auth/google/start","/auth/google/callback","/auth/passkey/options","/auth/passkey/login",
    "/auth/passkey/register-options","/auth/passkey/register","/chart.umd.min.js","/ewelink.js"}|set(ICON_FILES)
AUTH_PAGES={"/login":"login.html","/enrol":"login.html","/admin":"admin.html"}

BONDS_PREFIX="/bonds"

def bonds_path(path):
    return path==BONDS_PREFIX or path.startswith(BONDS_PREFIX+"/")

# A "back to the monitor" button added to the top-right of the tracker's header, in its own colours.
BONDS_BACK=(b'<style>header{position:relative}.brand{padding-right:7.5rem}'
    b'.modpi-back{position:absolute;top:12px;right:16px;padding:6px 12px;border:1px solid var(--line);border-radius:8px;'
    b'background:var(--surface);color:var(--ink);font-weight:600;text-decoration:none}</style>'
    b'<a class="modpi-back" href="/">&larr; Monitor</a>')

def rewrite_bonds_html(body):
    """The bond tracker links from its own root; point those links at /bonds/ instead, and add a way back."""
    for attr in (b'href="/',b'action="/',b'src="/'):
        body=body.replace(attr,attr[:-1]+BONDS_PREFIX.encode()+b"/")
    return body.replace(b"<header>",b"<header>"+BONDS_BACK,1)

def handler(store, web, voltage=None, tuya=None, ewelink=None, auth=None, bonds_port=None,
            juwai_local=None, juwai_source="cloud"):
    # Juwai readings come from one source: juwai_source ("cloud" or "local") picks what everyone sees.
    juwai_sources={"cloud":tuya,"local":juwai_local}
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"
        def setup(self):
            self.request.settimeout(10)
            super().setup()
        def send(self, status, body, kind="application/json; charset=utf-8", cache=None):
            if isinstance(body,dict):
                body=json.dumps(body,separators=(",",":"),allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type",kind)
            self.send_header("Content-Length",str(len(body)))
            self.send_header("Cache-Control",cache or ("no-store" if "json" in kind or "html" in kind else "public, max-age=300"))
            self.send_header("X-Content-Type-Options","nosniff")
            self.send_header("Referrer-Policy","no-referrer")
            for cookie in getattr(self,"cookies",()):
                self.send_header("Set-Cookie",cookie)
            self.end_headers()
            self.wfile.write(body)
        def redirect(self, location):
            self.send_response(303)
            self.send_header("Location",location)
            self.send_header("Cache-Control","no-store")
            self.send_header("Referrer-Policy","no-referrer")
            for cookie in getattr(self,"cookies",()):
                self.send_header("Set-Cookie",cookie)
            self.send_header("Content-Length","0")
            self.end_headers()
        def allowed(self, path):
            """True when the request may go on; otherwise it has been answered (sign-in page or 401)."""
            self.cookies=[];self.user=self.session_id=None
            if auth is None or not auth.enabled():
                return True
            found=auth.session(self.headers.get("Cookie"))
            if found:
                self.user,self.session_id,renewed=found
                if renewed: self.cookies.append(renewed)
                return True
            if path in AUTH_PUBLIC:
                return True
            if path in ("/","/index.html","/admin") or bonds_path(path): self.redirect("/login")
            else: self.send(401,{"error":"Sign in required"})
            return False
        def auth_post(self, path):
            if auth is None or not auth.enabled():
                return self.send(404,{"error":"Not found"})
            if not self.allowed(path):
                return
            settings=auth.settings()
            if settings.get("broken"):
                return self.send(503,{"error":"Login settings need fixing on the Pi"})
            if self.headers.get("Origin")!=settings["origin"]:
                LOG.warning("auth: refused POST %s from another origin",path);return self.send(403,{"error":"Wrong origin"})
            try:
                length=int(self.headers.get("Content-Length",""))
                if not 0<length<=65536: raise ValueError()
                body=json.loads(self.rfile.read(length))
                if not isinstance(body,dict): raise ValueError()
            except ValueError:
                return self.send(400,{"error":"Invalid request"})
            agent=self.headers.get("User-Agent","")[:300]
            try:
                if path=="/auth/passkey/options":
                    return self.send(200,auth.login_options())
                if path=="/auth/passkey/login":
                    self.cookies.append(auth.passkey_login(body,agent));return self.send(200,{"ok":True})
                if path=="/auth/passkey/register-options":
                    return self.send(200,auth.register_options(body,self.user))
                if path=="/auth/passkey/register":
                    cookie=auth.passkey_register(body,agent,self.user)
                    if cookie: self.cookies.append(cookie)
                    return self.send(200,{"ok":True})
                if self.user is None:
                    return self.send(401,{"error":"Sign in required"})
                if path=="/auth/links":
                    return self.send(200,auth.create_link(self.user,body.get("user")))
                if path=="/auth/passkeys/delete":
                    return self.send(200,auth.remove("passkeys",body.get("id")))
                if path=="/auth/sessions/delete":
                    return self.send(200,auth.remove("sessions",body.get("id")))
                if path=="/admin/power-off":
                    SHUTDOWN_REQUEST_FILE.write_text(str(int(time.time()))+"\n")
                    LOG.warning("power-off requested by %s from the Admin page",self.user)
                    return self.send(202,{"ok":True})
                if path=="/auth/logout":
                    self.cookies.append(auth.logout(self.session_id));return self.send(200,{"ok":True})
                return self.send(404,{"error":"Not found"})
            except AuthError as error:
                return self.send(error.status,{"error":str(error)})
            except ImportError:
                LOG.error("auth: python3-cryptography missing");return self.send(503,{"error":"Passkeys are unavailable on the Pi"})
            except (OSError,sqlite3.Error):
                LOG.exception("auth: storage error");return self.send(503,{"error":"Sign-in storage unavailable"})
        def proxy_bonds(self, method):
            """Premium Bond Tracker (Oscar's app, on this Pi at 127.0.0.1:bonds_port), only for signed-in users."""
            if bonds_port is None or auth is None or not auth.enabled() or not self.user:
                return self.send(404,{"error":"Not found"})
            body=None;headers={"Accept":self.headers.get("Accept","text/html")}
            if method=="POST":
                if self.headers.get("Origin")!=auth.settings()["origin"]:
                    return self.send(403,{"error":"Wrong origin"})
                try:
                    length=int(self.headers.get("Content-Length",""))
                    if not 0<=length<=1048576: raise ValueError()
                except ValueError:
                    return self.send(400,{"error":"Invalid request"})
                body=self.rfile.read(length)
                headers["Content-Type"]=self.headers.get("Content-Type","application/x-www-form-urlencoded")
            upstream=self.path[len(BONDS_PREFIX):] or "/"
            if not upstream.startswith("/"): upstream="/"+upstream
            try:
                conn=http.client.HTTPConnection("127.0.0.1",bonds_port,timeout=30)
                conn.request(method,upstream,body=body,headers=headers)
                reply=conn.getresponse();data=reply.read(8*1048576)
                conn.close()
            except OSError:
                LOG.warning("bonds: tracker not answering on port %s",bonds_port)
                return self.send(502,b"<!doctype html><meta name=viewport content='width=device-width'><body style='font-family:sans-serif;background:#090d16;color:#f8fafc;padding:2rem'><h1>Premium Bonds</h1><p>The bond tracker isn't running on the Pi right now. <a style='color:#38bdf8' href='/'>Back to the monitor</a></p>","text/html; charset=utf-8")
            location=reply.getheader("Location")
            if location:
                self.send_response(reply.status)
                self.send_header("Location",BONDS_PREFIX+location if location.startswith("/") else location)
                self.send_header("Cache-Control","no-store");self.send_header("Content-Length","0")
                for cookie in self.cookies: self.send_header("Set-Cookie",cookie)
                return self.end_headers()
            kind=reply.getheader("Content-Type","text/html; charset=utf-8")
            if "html" in kind: data=rewrite_bonds_html(data)
            return self.send(reply.status,data,kind,cache="private, no-store")
        def do_POST(self):
            path=urlsplit(self.path).path
            if path.startswith("/auth/") or path=="/admin/power-off":
                return self.auth_post(path)
            if bonds_path(path):
                if not self.allowed(path): return
                return self.proxy_bonds("POST")
            if path!="/api/deploy-hook":
                return self.send_error(501)
            try:
                length=int(self.headers.get("Content-Length",""))
            except ValueError:
                LOG.warning("deploy-hook: refused 411 (no length)");return self.send(411,{"error":"Length required"})
            if not 0<length<=262144:
                LOG.warning("deploy-hook: refused 413 (length %s)",length);return self.send(413,{"error":"Body too large"})
            body=self.rfile.read(length)
            try:
                secret=DEPLOY_SECRET_FILE.read_bytes().strip()
            except OSError:
                LOG.warning("deploy-hook: refused 503 (no secret file)");return self.send(503,{"error":"Deploy hook not configured"})
            if not secret:
                LOG.warning("deploy-hook: refused 503 (empty secret)");return self.send(503,{"error":"Deploy hook not configured"})
            expected="sha256="+hmac.new(secret,body,hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected,self.headers.get("X-Hub-Signature-256","")):
                LOG.warning("deploy-hook: refused 403 (bad signature, event %s, content type %s)",self.headers.get("X-GitHub-Event","-"),self.headers.get("Content-Type","-"));return self.send(403,{"error":"Bad signature"})
            event=self.headers.get("X-GitHub-Event","")
            if event=="ping":
                LOG.info("deploy-hook: ping ok");return self.send(200,{"ok":True})
            if event!="push":
                LOG.info("deploy-hook: ignored event %s",event);return self.send(202,{"queued":False})
            try:
                ref=json.loads(body).get("ref")
            except (ValueError,AttributeError):
                LOG.warning("deploy-hook: refused 400 (not JSON; content type %s)",self.headers.get("Content-Type","-"));return self.send(400,{"error":"Invalid payload"})
            if ref!=DEPLOY_BRANCH_REF:
                LOG.info("deploy-hook: ignored push to %s",ref);return self.send(202,{"queued":False})
            try:
                DEPLOY_REQUEST_FILE.write_text(str(int(time.time()))+"\n")
            except OSError:
                LOG.error("deploy-hook: could not write request file");return self.send(503,{"error":"Could not queue deploy"})
            LOG.info("deploy-hook: push to main queued");return self.send(202,{"queued":True})
        def do_GET(self):
            if len(self.path)>1024:
                return self.send(414,{"error":"Request too long"})
            parsed=urlsplit(self.path)
            query=parse_qs(parsed.query)
            if not self.allowed(parsed.path):
                return
            try:
                if parsed.path in AUTH_PAGES:
                    if auth is None or not auth.enabled() or (self.user and parsed.path=="/login"):
                        return self.redirect("/")
                    return self.send(200,(web/AUTH_PAGES[parsed.path]).read_bytes(),"text/html; charset=utf-8")
                if parsed.path=="/auth.js":
                    return self.send(200,(web/"auth.js").read_bytes(),"text/javascript; charset=utf-8")
                if parsed.path=="/auth/google/start" and auth is not None:
                    try:
                        url,cookie=auth.google_start()
                    except AuthError:
                        return self.redirect("/login?e=nogoogle")
                    self.cookies.append(cookie)
                    return self.redirect(url)
                if parsed.path=="/auth/google/callback" and auth is not None:
                    try:
                        self.cookies+=[auth.google_callback(query,self.headers.get("Cookie"),self.headers.get("User-Agent","")[:300]),
                                       "__Host-modpi-google=; Max-Age=0; Path=/; Secure; HttpOnly; SameSite=Lax"]
                    except AuthError as error:
                        return self.redirect("/login?e="+("denied" if error.status==403 else "failed"))
                    return self.redirect("/")
                if parsed.path=="/auth/state" and auth is not None and self.user:
                    if query: raise ValueError()
                    return self.send(200,dict(auth.state(self.user,self.session_id),bonds=bonds_port is not None))
                if bonds_path(parsed.path):
                    return self.proxy_bonds("GET")
                if parsed.path in ("/","/index.html"):
                    return self.send(200,(web/"index.html").read_bytes(),"text/html; charset=utf-8")
                if parsed.path=="/chart.umd.min.js":
                    return self.send(200,(web/"chart.umd.min.js").read_bytes(),"text/javascript; charset=utf-8")
                if parsed.path in ICON_FILES:
                    return self.send(200,(web/parsed.path[1:]).read_bytes(),ICON_FILES[parsed.path])
                if parsed.path=="/ewelink.js":
                    return self.send(200,(web/"ewelink.js").read_bytes(),"text/javascript; charset=utf-8")
                if parsed.path=="/api/integrations/ewelink/callback" and ewelink is not None:
                    try:
                        ewelink.callback(query)
                    except (ValueError, OSError, RuntimeError, KeyError):
                        return self.send(400,{"error":"Authorisation failed or expired. Start a new connection."})
                    except Exception:
                        return self.send(502,{"error":"eWeLink authorisation exchange failed. Start a new connection."})
                    return self.redirect("/#ewelink")
                if parsed.path=="/api/ewelink/latest" and ewelink is not None:
                    if query: raise ValueError()
                    return self.send(200,ewelink.latest())
                if parsed.path=="/api/home/latest" and ewelink is not None:
                    if query: raise ValueError()
                    return self.send(200,ewelink.home_latest())
                if parsed.path=="/api/home/history" and ewelink is not None:
                    if set(query)!={"device"} or len(query["device"])!=1: raise ValueError()
                    return self.send(200,ewelink.home_history(query["device"][0]))
                if parsed.path=="/api/ewelink/history" and ewelink is not None:
                    if set(query)!={"device","window"} or any(len(v)!=1 for v in query.values()): raise ValueError()
                    return self.send(200,ewelink.history(query["device"][0],query["window"][0]))
                if parsed.path in ("/api/tuya/latest","/api/tuya/history"):
                    # While local reads are on test, only signed-in users may pick the other source.
                    source=juwai_source
                    if "source" in query:
                        picked=query.pop("source")
                        if len(picked)!=1 or picked[0] not in juwai_sources or not self.user:
                            raise ValueError()
                        source=picked[0]
                    reader=juwai_sources[source]
                    if reader is None:
                        return self.send(404,{"error":"Not found"})
                    if parsed.path=="/api/tuya/latest":
                        if query:
                            raise ValueError()
                        return self.send(200,dict(reader.latest(),source=source,
                            switchable=bool(self.user and tuya is not None and juwai_local is not None)))
                    if set(query)!={"device","window"} or any(len(v)!=1 for v in query.values()):
                        raise ValueError()
                    return self.send(200,dict(reader.history(query["device"][0],query["window"][0]),source=source))
                if parsed.path=="/api/live":
                    since=int(query.get("since",["0"])[0])
                    if not 0<=since<=int(time.time()*1000)+60000:
                        raise ValueError()
                    window=query.get("window",[None])[0]
                    if window is not None and window not in WINDOWS:
                        raise ValueError()
                    return self.send(200,store.live(since,window=window))
                if parsed.path=="/api/voltage" and voltage is not None:
                    if query:
                        raise ValueError()
                    try:
                        return self.send(200,voltage.sample())
                    except OSError:
                        return self.send(503,{"error":"Voltage unavailable"})
                if parsed.path=="/api/history":
                    if "start" in query or "end" in query:
                        if "start" not in query or "end" not in query or "window" in query:
                            raise ValueError()
                        return self.send(200,store.history(start=int(query["start"][0]),end=int(query["end"][0])))
                    window=query.get("window",["15m"])[0]
                    if window not in WINDOWS:
                        raise ValueError()
                    return self.send(200,store.history(window))
                if parsed.path=="/version":
                    if query: raise ValueError()
                    try:
                        info=json.loads(VERSION_FILE.read_text())
                    except (OSError,ValueError):
                        info={}
                    return self.send(200,{k:info.get(k) for k in ("commit","deployed_at","subject")})
                if parsed.path=="/health":
                    live=store.live()
                    return self.send(200 if not live["stale"] else 503,{"ok":not live["stale"],"latest_ts":live["latest"]["ts"] if live["latest"] else None})
                return self.send(404,{"error":"Not found"})
            except AuthError as error:
                return self.send(error.status,{"error":str(error)})
            except ValueError:
                return self.send(400,{"error":"Invalid query"})
            except (OSError,RuntimeError,sqlite3.Error):
                return self.send(503,{"error":"Storage unavailable"})
        def log_message(self,*args):
            pass
    return Handler

class WebServer(ThreadingHTTPServer):
    """Bound concurrent requests on the router's limited RAM."""
    daemon_threads=True
    def __init__(self,*args,**kwargs):
        self.slots=threading.BoundedSemaphore(12)
        super().__init__(*args,**kwargs)
    def process_request(self,request,address):
        if not self.slots.acquire(blocking=False):
            try:
                request.sendall(b'HTTP/1.0 503 Busy\r\nContent-Length: 0\r\n\r\n')
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request,address)
        except BaseException:
            self.slots.release()
            raise
    def process_request_thread(self,request,address):
        try:
            super().process_request_thread(request,address)
        finally:
            self.slots.release()

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",default="/etc/modbus-monitor.json")
    parser.add_argument("--probe",action="store_true")
    args=parser.parse_args()
    config=json.loads(Path(args.config).read_text())
    logging.basicConfig(level=logging.INFO,format="%(levelname)s %(message)s")
    if args.probe:
        print(json.dumps({"watts":read_power(config["meter_host"],config.get("meter_port",8838))}))
        return
    sys.path.insert(0,"/usr/local/usr/lib/clockmill")
    import modbus_usb
    # Pin the USB directory for the entire SQLite connection lifetime. No fallback.
    with modbus_usb.storage_directory() as directory:
        guard=lambda: modbus_usb.check()
        store=Store("/proc/self/fd/"+str(directory)+"/monitor.sqlite3",guard)
        monitor=Monitor(store,config["meter_host"],config.get("meter_port",8838))
        voltage=VoltageReader(config["meter_host"],config.get("meter_port",8838))
        from tuya_cached import CachedTuya
        tuya=CachedTuya(modbus_usb.storage_directory,modbus_usb.check)
        from ewelink import Ewelink
        ewelink=Ewelink(modbus_usb.storage_directory,modbus_usb.check)
        ewelink_thread=threading.Thread(target=ewelink.poll,daemon=True)
        ewelink_thread.start()
        server=WebServer((config.get("bind","127.0.0.1"),config.get("port",8096)),handler(store,Path(__file__).parent/"web",voltage,tuya,ewelink))
        server.daemon_threads=True
        thread=threading.Thread(target=monitor.poll,daemon=True)
        thread.start()
        def stop(*_):
            ewelink.stop.set()
            monitor.stop.set()
            threading.Thread(target=server.shutdown,daemon=True).start()
        signal.signal(signal.SIGTERM,stop)
        signal.signal(signal.SIGINT,stop)
        LOG.info("Read-only dashboard listening; continuous USB collection enabled")
        try:
            server.serve_forever(poll_interval=.5)
        finally:
            ewelink.stop.set()
            monitor.stop.set()
            thread.join(timeout=8)
            server.server_close()
            store.db.close()

if __name__=="__main__":
    main()
