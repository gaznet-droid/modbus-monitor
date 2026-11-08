"""Login for the whole site: Google sign-in for allowlisted emails, passkeys, and one-time sign-up links.

Switched off unless the private settings file exists with "enabled": true. A settings file that
exists but is invalid keeps the site locked (only the public paths answer) rather than open.

Settings (private, never in the repo):
    {"enabled": true, "origin": "https://modbus.clockmillbarn.uk",
     "users": {"gareth": {"email": "...", "name": "Gareth"}, "dad": {"email": "...", "name": "Dad"}},
     "google": {"client_id": "...", "client_secret": "..."}}

State (sessions, passkeys, sign-up links) lives in a private SQLite file. Only hashes of session
and link tokens are stored. Passkey signatures need python3-cryptography, imported only when used.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import secrets
import sqlite3
import struct
import threading
import time
import urllib.parse
import urllib.request

LOG = logging.getLogger("modpi-auth")
SESSION_COOKIE = "__Host-modpi"
STATE_COOKIE = "__Host-modpi-google"
SESSION_SECONDS = 180 * 86400        # rolling: renewed on use, so a device used now and then stays signed in
COOKIE_SECONDS = 400 * 86400         # browsers cap cookie lifetime at 400 days
RENEW_SECONDS = 3600
LINK_SECONDS = 24 * 3600
CHALLENGE_SECONDS = 300
GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
USER_KEY = re.compile(r"[a-z0-9-]{1,32}")
EMAIL = re.compile(r"[^@\s]{1,64}@[^@\s]{1,190}")
ALG_ES256, ALG_RS256 = -7, -257

class AuthError(Exception):
    """A refusal shown to the person signing in. Never carries secrets."""
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status

def b64e(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

def b64d(text):
    if not isinstance(text, str) or len(text) > 8192 or not re.fullmatch(r"[A-Za-z0-9_-]*", text):
        raise AuthError("Invalid encoding")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))

def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()

def cbor(data, i=0, depth=0):
    """Minimal CBOR decoder for WebAuthn attestation objects and COSE keys. Returns (value, next index)."""
    if depth > 8 or i >= len(data):
        raise AuthError("Invalid passkey data")
    head = data[i]; major, info = head >> 5, head & 31; i += 1
    if info < 24:
        value = info
    elif info in (24, 25, 26, 27):
        size = {24: 1, 25: 2, 26: 4, 27: 8}[info]
        if i + size > len(data):
            raise AuthError("Invalid passkey data")
        value = int.from_bytes(data[i:i + size], "big"); i += size
    else:
        raise AuthError("Invalid passkey data")
    if major == 0:
        return value, i
    if major == 1:
        return -1 - value, i
    if major in (2, 3):
        if i + value > len(data):
            raise AuthError("Invalid passkey data")
        chunk = bytes(data[i:i + value])
        return (chunk if major == 2 else chunk.decode()), i + value
    if major == 4:
        items = []
        for _ in range(value):
            item, i = cbor(data, i, depth + 1); items.append(item)
        return items, i
    if major == 5:
        items = {}
        for _ in range(value):
            key, i = cbor(data, i, depth + 1)
            items[key], i = cbor(data, i, depth + 1)
        return items, i
    if major == 7 and info in (20, 21, 22):
        return {20: False, 21: True, 22: None}[info], i
    raise AuthError("Invalid passkey data")

def parse_auth_data(data):
    """Returns (rp id hash, flags, sign count, (credential id, COSE key) or None)."""
    if len(data) < 37:
        raise AuthError("Invalid passkey data")
    flags = data[32]; count = struct.unpack(">I", data[33:37])[0]; credential = None
    if flags & 0x40:
        if len(data) < 55:
            raise AuthError("Invalid passkey data")
        size = struct.unpack(">H", data[53:55])[0]
        cred_id = data[55:55 + size]
        if len(cred_id) != size or not 16 <= size <= 1023:
            raise AuthError("Invalid passkey data")
        key, _ = cbor(data, 55 + size)
        credential = (bytes(cred_id), key)
    return data[:32], flags, count, credential

def cose_public_key(key):
    """COSE public key -> (algorithm, DER SubjectPublicKeyInfo). ES256 and RS256 only."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec, rsa
    if not isinstance(key, dict):
        raise AuthError("Unsupported passkey")
    alg = key.get(3)
    try:
        if key.get(1) == 2 and alg == ALG_ES256 and key.get(-1) == 1:
            x, y = key.get(-2), key.get(-3)
            if not (isinstance(x, bytes) and isinstance(y, bytes) and len(x) == len(y) == 32):
                raise AuthError("Unsupported passkey")
            public = ec.EllipticCurvePublicNumbers(int.from_bytes(x, "big"), int.from_bytes(y, "big"), ec.SECP256R1()).public_key()
        elif key.get(1) == 3 and alg == ALG_RS256:
            n, e = key.get(-1), key.get(-2)
            if not (isinstance(n, bytes) and isinstance(e, bytes) and len(n) >= 256):
                raise AuthError("Unsupported passkey")
            public = rsa.RSAPublicNumbers(int.from_bytes(e, "big"), int.from_bytes(n, "big")).public_key()
        else:
            raise AuthError("Unsupported passkey")
    except ValueError:
        raise AuthError("Unsupported passkey")
    return alg, public.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)

def verify_signature(alg, der, signature, data):
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, padding
    public = serialization.load_der_public_key(der)
    try:
        if alg == ALG_ES256:
            public.verify(signature, data, ec.ECDSA(hashes.SHA256()))
        elif alg == ALG_RS256:
            public.verify(signature, data, padding.PKCS1v15(), hashes.SHA256())
        else:
            raise AuthError("Unsupported passkey", 403)
    except InvalidSignature:
        raise AuthError("Passkey check failed", 403)

def device_label(agent):
    agent = agent or ""
    for marker, label in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("Macintosh", "Mac"),
                          ("Windows", "Windows"), ("CrOS", "Chromebook"), ("Linux", "Linux")):
        if marker in agent:
            return label
    return "Browser"

def cookie_value(header, name):
    for part in (header or "").split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return None

def set_cookie(name, value, seconds):
    return "%s=%s; Max-Age=%d; Path=/; Secure; HttpOnly; SameSite=Lax" % (name, value, seconds)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, user TEXT NOT NULL,
  method TEXT NOT NULL, device TEXT NOT NULL, created INTEGER NOT NULL, seen INTEGER NOT NULL, expires INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS passkeys(id TEXT PRIMARY KEY, user TEXT NOT NULL, alg INTEGER NOT NULL, public_key BLOB NOT NULL,
  sign_count INTEGER NOT NULL, device TEXT NOT NULL, created INTEGER NOT NULL, used INTEGER);
CREATE TABLE IF NOT EXISTS links(token_hash TEXT PRIMARY KEY, user TEXT NOT NULL, created_by TEXT NOT NULL,
  created INTEGER NOT NULL, expires INTEGER NOT NULL, used INTEGER);
"""

class Auth:
    def __init__(self, settings_path, db_path, fetch=None, clock=time.time):
        self.settings_path = Path(settings_path); self.db_path = Path(db_path)
        self.fetch = fetch or self._fetch; self.clock = clock
        self.lock = threading.Lock(); self.db = None
        self.challenges = {}; self.google_states = {}
        self._settings = None; self._settings_key = None

    # Settings ---------------------------------------------------------------------------------
    def settings(self):
        """Validated settings, re-read when the file changes. None when switched off."""
        try:
            info = self.settings_path.stat(); key = (info.st_mtime_ns, info.st_size)
        except FileNotFoundError:
            return None
        if key != self._settings_key:
            self._settings = self._load(); self._settings_key = key
        return self._settings

    def _load(self):
        try:
            data = json.loads(self.settings_path.read_text())
        except (OSError, ValueError):
            LOG.error("auth: settings unreadable; site stays locked")
            return {"broken": True}
        if data.get("enabled") is not True:
            return None
        users, origin, google = data.get("users"), data.get("origin"), data.get("google")
        parsed = urllib.parse.urlsplit(origin if isinstance(origin, str) else "")
        ok = (parsed.scheme == "https" and parsed.hostname and not parsed.path.strip("/") and not parsed.query
              and isinstance(users, dict) and 1 <= len(users) <= 10)
        clean = {}
        for name, user in (users.items() if ok else ()):
            if not (USER_KEY.fullmatch(str(name)) and isinstance(user, dict) and isinstance(user.get("email"), str)
                    and EMAIL.fullmatch(user["email"]) and isinstance(user.get("name", name), str) and 1 <= len(user.get("name", name)) <= 40):
                ok = False; break
            clean[name] = {"email": user["email"].strip().lower(), "name": user.get("name", name)}
        if ok and google is not None:
            ok = isinstance(google, dict) and all(isinstance(google.get(k), str) and google[k] for k in ("client_id", "client_secret"))
        if not ok:
            LOG.error("auth: settings invalid; site stays locked")
            return {"broken": True}
        return {"origin": "https://" + parsed.netloc, "rp_id": parsed.hostname, "users": clean, "google": google}

    def enabled(self):
        return self.settings() is not None

    def _ready(self):
        settings = self.settings()
        if settings is None:
            raise AuthError("Login is switched off", 404)
        if settings.get("broken"):
            raise AuthError("Login settings need fixing on the Pi", 503)
        return settings

    # Storage ----------------------------------------------------------------------------------
    def _conn(self):
        if self.db is None:
            if not self.db_path.exists():
                os.close(os.open(self.db_path, os.O_CREAT | os.O_WRONLY, 0o600))
            self.db = sqlite3.connect(self.db_path, check_same_thread=False, timeout=5)
            self.db.executescript(SCHEMA)
        return self.db

    def _prune(self, now):
        for store in (self.challenges, self.google_states):
            for key in [k for k, v in store.items() if v["expires"] < now]:
                del store[key]

    # Sessions ---------------------------------------------------------------------------------
    def _new_session(self, user, method, agent):
        now = int(self.clock()); token = secrets.token_urlsafe(32)
        with self.lock:
            db = self._conn()
            db.execute("INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?)", (secrets.token_urlsafe(9), token_hash(token), user,
                       method, device_label(agent), now, now, now + SESSION_SECONDS))
            db.commit()
        LOG.info("auth: %s signed in with %s", user, method)
        return set_cookie(SESSION_COOKIE, token, COOKIE_SECONDS)

    def session(self, cookie_header):
        """(user, session id, renewed cookie or None) for a valid session, else None."""
        settings = self.settings()
        token = cookie_value(cookie_header, SESSION_COOKIE)
        if not settings or settings.get("broken") or not token or len(token) > 100:
            return None
        now = int(self.clock())
        with self.lock:
            db = self._conn()
            row = db.execute("SELECT id,user,seen FROM sessions WHERE token_hash=? AND expires>?", (token_hash(token), now)).fetchone()
            if not row or row[1] not in settings["users"]:
                return None
            renewed = None
            if now - row[2] >= RENEW_SECONDS:
                db.execute("UPDATE sessions SET seen=?,expires=? WHERE id=?", (now, now + SESSION_SECONDS, row[0])); db.commit()
                renewed = set_cookie(SESSION_COOKIE, token, COOKIE_SECONDS)
        return row[1], row[0], renewed

    def logout(self, session_id):
        with self.lock:
            db = self._conn(); db.execute("DELETE FROM sessions WHERE id=?", (session_id,)); db.commit()
        return set_cookie(SESSION_COOKIE, "", 0)

    # Google -----------------------------------------------------------------------------------
    def _redirect_uri(self, settings):
        return settings["origin"] + "/auth/google/callback"

    def google_start(self):
        settings = self._ready()
        if not settings["google"]:
            raise AuthError("Google sign-in is not set up", 404)
        now = self.clock(); state = secrets.token_urlsafe(24); nonce = secrets.token_urlsafe(24)
        with self.lock:
            self._prune(now)
            if len(self.google_states) > 50:
                raise AuthError("Too many sign-in attempts; try again shortly", 429)
            self.google_states[state] = {"nonce": nonce, "expires": now + 600}
        query = urllib.parse.urlencode({"client_id": settings["google"]["client_id"], "redirect_uri": self._redirect_uri(settings),
            "response_type": "code", "scope": "openid email", "state": state, "nonce": nonce, "prompt": "select_account"})
        return GOOGLE_AUTH + "?" + query, set_cookie(STATE_COOKIE, state, 600)

    def google_callback(self, query, cookie_header, agent):
        """Returns the session cookie for an allowlisted Google account."""
        settings = self._ready()
        if not settings["google"]:
            raise AuthError("Google sign-in is not set up", 404)
        if "error" in query:
            raise AuthError("Google sign-in was cancelled")
        state = (query.get("state") or [""])[0]; code = (query.get("code") or [""])[0]
        browser_state = cookie_value(cookie_header, STATE_COOKIE) or ""
        with self.lock:
            self._prune(self.clock())
            pending = self.google_states.pop(state, None) if state else None
        if not pending or not code or not hmac.compare_digest(state, browser_state):
            raise AuthError("Sign-in expired; please try again")
        reply = self.fetch(GOOGLE_TOKEN, {"code": code, "client_id": settings["google"]["client_id"],
            "client_secret": settings["google"]["client_secret"], "redirect_uri": self._redirect_uri(settings),
            "grant_type": "authorization_code"})
        # The ID token comes straight from Google's token endpoint over TLS, so (OpenID Connect Core 3.1.3.7)
        # TLS stands in for the signature check; the claims are still checked here.
        try:
            claims = json.loads(b64d(reply["id_token"].split(".")[1]))
        except (KeyError, IndexError, AttributeError, ValueError, AuthError):
            raise AuthError("Google sign-in failed", 502)
        now = self.clock()
        if (claims.get("iss") not in GOOGLE_ISSUERS or claims.get("aud") != settings["google"]["client_id"]
                or not isinstance(claims.get("exp"), (int, float)) or claims["exp"] < now
                or not hmac.compare_digest(str(claims.get("nonce", "")), pending["nonce"])):
            raise AuthError("Google sign-in failed", 403)
        email = str(claims.get("email", "")).strip().lower()
        user = next((name for name, u in settings["users"].items() if u["email"] == email), None)
        if not user or claims.get("email_verified") is not True:
            LOG.warning("auth: Google account not on the allowlist")
            raise AuthError("That Google account is not allowed", 403)
        return self._new_session(user, "Google", agent)

    @staticmethod
    def _fetch(url, data):
        request = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=10) as reply:
                return json.loads(reply.read(65536))
        except (OSError, ValueError):
            raise AuthError("Google sign-in failed", 502)

    # Passkeys ---------------------------------------------------------------------------------
    @staticmethod
    def user_handle(user):
        return hashlib.sha256(b"modpi-user:" + user.encode()).digest()[:16]

    def _challenge(self, kind, **extra):
        now = self.clock(); cid = secrets.token_urlsafe(12); challenge = secrets.token_bytes(32)
        with self.lock:
            self._prune(now)
            if len(self.challenges) > 500:
                raise AuthError("Too many attempts; try again shortly", 429)
            self.challenges[cid] = dict(extra, kind=kind, challenge=challenge, expires=now + CHALLENGE_SECONDS)
        return cid, b64e(challenge)

    def _take_challenge(self, cid, kind):
        with self.lock:
            self._prune(self.clock())
            entry = self.challenges.pop(cid, None) if isinstance(cid, str) else None
        if not entry or entry["kind"] != kind:
            raise AuthError("This attempt expired; please try again")
        return entry

    def _client_data(self, settings, body, kind, challenge):
        raw = b64d(body.get("clientDataJSON"))
        try:
            data = json.loads(raw)
        except ValueError:
            raise AuthError("Invalid passkey data")
        if (data.get("type") != kind or data.get("origin") != settings["origin"]
                or not hmac.compare_digest(str(data.get("challenge", "")), b64e(challenge))):
            raise AuthError("Passkey check failed", 403)
        return raw

    def login_options(self):
        settings = self._ready()
        cid, challenge = self._challenge("get")
        return {"challengeId": cid, "publicKey": {"challenge": challenge, "rpId": settings["rp_id"], "timeout": 60000,
                "userVerification": "required", "allowCredentials": []}}

    def passkey_login(self, body, agent):
        settings = self._ready()
        entry = self._take_challenge(body.get("challengeId"), "get")
        raw = self._client_data(settings, body, "webauthn.get", entry["challenge"])
        auth_data = b64d(body.get("authenticatorData")); signature = b64d(body.get("signature"))
        cred_id = body.get("id")
        with self.lock:
            row = self._conn().execute("SELECT user,alg,public_key,sign_count FROM passkeys WHERE id=?", (cred_id,)).fetchone() if isinstance(cred_id, str) else None
        if not row or row[0] not in settings["users"]:
            raise AuthError("This passkey isn't recognised. It may have been removed.", 403)
        rp_hash, flags, count, _ = parse_auth_data(auth_data)
        if not hmac.compare_digest(rp_hash, hashlib.sha256(settings["rp_id"].encode()).digest()) or flags & 0x05 != 0x05:
            raise AuthError("Passkey check failed", 403)
        if body.get("userHandle") and b64d(body["userHandle"]) != self.user_handle(row[0]):
            raise AuthError("Passkey check failed", 403)
        verify_signature(row[1], row[2], signature, auth_data + hashlib.sha256(raw).digest())
        if count and row[3] and count <= row[3]:
            LOG.warning("auth: passkey counter went backwards for %s; refused", row[0])
            raise AuthError("Passkey check failed", 403)
        with self.lock:
            db = self._conn(); db.execute("UPDATE passkeys SET sign_count=?,used=? WHERE id=?", (count, int(self.clock()), cred_id)); db.commit()
        return self._new_session(row[0], "passkey", agent)

    def _link_user(self, token, settings):
        if not isinstance(token, str) or not 20 <= len(token) <= 100:
            raise AuthError("This sign-up link isn't valid", 403)
        with self.lock:
            row = self._conn().execute("SELECT user FROM links WHERE token_hash=? AND used IS NULL AND expires>?",
                                       (token_hash(token), int(self.clock()))).fetchone()
        if not row or row[0] not in settings["users"]:
            raise AuthError("This sign-up link has expired or was already used. Ask for a new one.", 403)
        return row[0]

    def register_options(self, body, signed_in):
        """Passkey creation options, for a signed-in user or the holder of a sign-up link."""
        settings = self._ready()
        link = body.get("link")
        user = self._link_user(link, settings) if link else signed_in
        if not user:
            raise AuthError("Sign in first", 401)
        with self.lock:
            existing = [r[0] for r in self._conn().execute("SELECT id FROM passkeys WHERE user=?", (user,))]
        cid, challenge = self._challenge("create", user=user, link=token_hash(link) if link else None)
        info = settings["users"][user]
        return {"challengeId": cid, "name": info["name"], "publicKey": {"challenge": challenge, "timeout": 120000,
            "rp": {"id": settings["rp_id"], "name": "Modbus monitor"},
            "user": {"id": b64e(self.user_handle(user)), "name": info["email"], "displayName": info["name"]},
            "pubKeyCredParams": [{"type": "public-key", "alg": ALG_ES256}, {"type": "public-key", "alg": ALG_RS256}],
            "authenticatorSelection": {"residentKey": "required", "requireResidentKey": True, "userVerification": "required"},
            "attestation": "none", "excludeCredentials": [{"type": "public-key", "id": x} for x in existing]}}

    def passkey_register(self, body, agent, signed_in):
        """Stores a new passkey. Returns a session cookie when the device wasn't already signed in."""
        settings = self._ready()
        entry = self._take_challenge(body.get("challengeId"), "create")
        if not entry["link"] and entry["user"] != signed_in:
            raise AuthError("Sign in first", 401)
        self._client_data(settings, body, "webauthn.create", entry["challenge"])
        attestation, _ = cbor(b64d(body.get("attestationObject")))
        if not isinstance(attestation, dict) or not isinstance(attestation.get("authData"), bytes):
            raise AuthError("Invalid passkey data")
        rp_hash, flags, count, credential = parse_auth_data(attestation["authData"])
        if not hmac.compare_digest(rp_hash, hashlib.sha256(settings["rp_id"].encode()).digest()) or flags & 0x45 != 0x45 or not credential:
            raise AuthError("Passkey check failed", 403)
        cred_id = b64e(credential[0])
        if body.get("id") != cred_id:
            raise AuthError("Passkey check failed", 403)
        alg, der = cose_public_key(credential[1])
        now = int(self.clock()); user = entry["user"]
        with self.lock:
            db = self._conn()
            if entry["link"]:
                used = db.execute("UPDATE links SET used=? WHERE token_hash=? AND used IS NULL AND expires>?", (now, entry["link"], now))
                if used.rowcount != 1:
                    db.rollback(); raise AuthError("This sign-up link has expired or was already used. Ask for a new one.", 403)
            try:
                db.execute("INSERT INTO passkeys VALUES(?,?,?,?,?,?,?,NULL)", (cred_id, user, alg, der, count, device_label(agent), now))
            except sqlite3.IntegrityError:
                db.rollback(); raise AuthError("This passkey is already set up")
            db.commit()
        LOG.info("auth: passkey added for %s", user)
        return None if signed_in == user else self._new_session(user, "passkey", agent)

    # Admin ------------------------------------------------------------------------------------
    def create_link(self, by_user, for_user):
        settings = self._ready()
        if for_user not in settings["users"]:
            raise AuthError("Unknown person")
        token = secrets.token_urlsafe(32); now = int(self.clock())
        with self.lock:
            db = self._conn()
            db.execute("DELETE FROM links WHERE expires<?", (now - 30 * 86400,))
            db.execute("INSERT INTO links VALUES(?,?,?,?,?,NULL)", (token_hash(token), for_user, by_user, now, now + LINK_SECONDS)); db.commit()
        LOG.info("auth: %s created a sign-up link for %s", by_user, for_user)
        return {"url": settings["origin"] + "/enrol?t=" + token, "expires": (now + LINK_SECONDS) * 1000, "for": settings["users"][for_user]["name"]}

    def state(self, user, session_id):
        settings = self._ready()
        with self.lock:
            db = self._conn()
            passkeys = [{"id": r[0], "user": r[1], "device": r[2], "created": r[3] * 1000, "used": r[4] * 1000 if r[4] else None}
                        for r in db.execute("SELECT id,user,device,created,used FROM passkeys ORDER BY created")]
            sessions = [{"id": r[0], "user": r[1], "method": r[2], "device": r[3], "created": r[4] * 1000, "seen": r[5] * 1000,
                         "current": r[0] == session_id}
                        for r in db.execute("SELECT id,user,method,device,created,seen FROM sessions WHERE expires>? ORDER BY seen DESC", (int(self.clock()),))]
        return {"me": user, "google": bool(settings["google"]),
                "users": [{"key": k, "name": v["name"]} for k, v in settings["users"].items()],
                "passkeys": passkeys, "sessions": sessions}

    def remove(self, table, item_id):
        if table not in ("passkeys", "sessions") or not isinstance(item_id, str):
            raise AuthError("Invalid request")
        with self.lock:
            db = self._conn(); gone = db.execute("DELETE FROM %s WHERE id=?" % table, (item_id,)).rowcount; db.commit()
        if not gone:
            raise AuthError("Already removed", 404)
        return {"ok": True}
