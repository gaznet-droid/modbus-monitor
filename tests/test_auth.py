import hashlib
import json
from pathlib import Path
import struct
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
import auth
import monitor

try:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    CRYPTO = True
except ImportError:
    CRYPTO = False

ORIGIN = 'https://modbus.example.test'

def cbor_encode(value):
    def head(major, n):
        if n < 24: return bytes([major << 5 | n])
        for info, size in ((24, 1), (25, 2), (26, 4), (27, 8)):
            if n < 1 << (8 * size): return bytes([major << 5 | info]) + n.to_bytes(size, 'big')
    if isinstance(value, bool): return bytes([0xf5 if value else 0xf4])
    if isinstance(value, int): return head(0, value) if value >= 0 else head(1, -1 - value)
    if isinstance(value, bytes): return head(2, len(value)) + value
    if isinstance(value, str): return head(3, len(value.encode())) + value.encode()
    if isinstance(value, dict): return head(5, len(value)) + b''.join(cbor_encode(k) + cbor_encode(v) for k, v in value.items())
    raise TypeError(value)

class Phone:
    """A pretend authenticator: an ES256 key and a credential id."""
    def __init__(self, count=0):
        self.key = ec.generate_private_key(ec.SECP256R1()); self.id = b'phone-credential-' + bytes(8); self.count = count
    def cose(self):
        n = self.key.public_key().public_numbers()
        return cbor_encode({1: 2, 3: -7, -1: 1, -2: n.x.to_bytes(32, 'big'), -3: n.y.to_bytes(32, 'big')})
    def create(self, options, origin=ORIGIN):
        rp = hashlib.sha256(options['publicKey']['rp']['id'].encode()).digest()
        data = rp + bytes([0x45]) + struct.pack('>I', 0) + bytes(16) + struct.pack('>H', len(self.id)) + self.id + self.cose()
        client = json.dumps({'type': 'webauthn.create', 'challenge': options['publicKey']['challenge'], 'origin': origin}).encode()
        return {'challengeId': options['challengeId'], 'id': auth.b64e(self.id), 'clientDataJSON': auth.b64e(client),
                'attestationObject': auth.b64e(cbor_encode({'fmt': 'none', 'attStmt': {}, 'authData': data}))}
    def get(self, options, origin=ORIGIN, count=None):
        self.count = self.count + 1 if count is None else count
        data = hashlib.sha256(options['publicKey']['rpId'].encode()).digest() + bytes([0x05]) + struct.pack('>I', self.count)
        client = json.dumps({'type': 'webauthn.get', 'challenge': options['publicKey']['challenge'], 'origin': origin}).encode()
        signature = self.key.sign(data + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        return {'challengeId': options['challengeId'], 'id': auth.b64e(self.id), 'clientDataJSON': auth.b64e(client),
                'authenticatorData': auth.b64e(data), 'signature': auth.b64e(signature), 'userHandle': None}

def session_cookie(response):
    for value in response.headers.get_all('Set-Cookie') or []:
        name, _, rest = value.partition('=')
        if name == auth.SESSION_COOKIE:
            token = rest.split(';')[0]
            return auth.SESSION_COOKIE + '=' + token if token else ''
    return None

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw): return None

class AuthHttpBase(unittest.TestCase):
    bonds_port = None
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); root = Path(self.tmp.name)
        self.settings = root / 'auth.json'; self.now = [1_800_000_000.0]; self.claims = {}
        self.auth = auth.Auth(self.settings, root / 'auth.sqlite3', fetch=self.fake_google, clock=lambda: self.now[0])
        class Store:
            def live(self, *a, **kw): return {'stale': False, 'latest': {'ts': 1}}
        web = Path(monitor.__file__).parent / 'web'
        self.server = monitor.WebServer(('127.0.0.1', 0), monitor.handler(Store(), web, auth=self.auth, bonds_port=self.bonds_port))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)
    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        if self.auth.db: self.auth.db.close()
        self.tmp.cleanup()
    def enable(self, **extra):
        data = {'enabled': True, 'origin': ORIGIN, 'users': {'gareth': {'email': 'Gareth@Example.test', 'name': 'Gareth'},
                'dad': {'email': 'dad@example.test', 'name': 'Dad'}}, 'google': {'client_id': 'cid', 'client_secret': 'shh'}}
        data.update(extra); self.settings.write_text(json.dumps(data))
    def fake_google(self, url, data):
        self.assertEqual(url, auth.GOOGLE_TOKEN); self.assertEqual(data['client_secret'], 'shh')
        self.assertEqual(data['redirect_uri'], ORIGIN + '/auth/google/callback')
        payload = auth.b64e(json.dumps(self.claims).encode())
        return {'id_token': 'header.' + payload + '.signature'}
    def request(self, path, method='GET', body=None, cookie=None, origin=ORIGIN):
        headers = {'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)'}
        if cookie: headers['Cookie'] = cookie
        if body is not None: headers.update({'Content-Type': 'application/json', 'Origin': origin})
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try: return urllib.request.build_opener(NoRedirect).open(req, timeout=3)
        except urllib.error.HTTPError as error: return error
    def status(self, path, **kw):
        with self.request(path, **kw) as r: return r.status, r.headers.get('Location')
    def post(self, path, body, cookie=None, origin=ORIGIN):
        with self.request(path, 'POST', body, cookie, origin) as r:
            return r.status, json.loads(r.read() or b'{}'), session_cookie(r)
    def google(self, email='gareth@example.test', **claims):
        with self.request('/auth/google/start') as r:
            self.assertEqual(r.status, 303); location = urllib.parse.urlsplit(r.headers['Location'])
            state_cookie = r.headers['Set-Cookie'].split(';')[0]
        q = urllib.parse.parse_qs(location.query); self.assertEqual(q['client_id'], ['cid'])
        self.claims = {'iss': 'https://accounts.google.com', 'aud': 'cid', 'exp': self.now[0] + 300, 'nonce': q['nonce'][0],
                       'email': email, 'email_verified': True}; self.claims.update(claims)
        with self.request('/auth/google/callback?' + urllib.parse.urlencode({'state': q['state'][0], 'code': 'c'}), cookie=state_cookie) as r:
            return r.headers['Location'], session_cookie(r)

class AuthHttpTests(AuthHttpBase):
    def test_off_by_default_changes_nothing(self):
        self.assertEqual(self.status('/')[0], 200)
        self.assertEqual(self.status('/login'), (303, '/'))
        self.assertEqual(self.status('/admin'), (303, '/'))
        self.assertEqual(self.status('/auth/state')[0], 404)
        self.assertEqual(self.post('/auth/passkey/options', {})[0], 404)
        self.settings.write_text(json.dumps({'enabled': False}))
        self.assertEqual(self.status('/')[0], 200)

    def test_whole_site_locked_except_public_paths(self):
        self.enable()
        self.assertEqual(self.status('/'), (303, '/login'))
        self.assertEqual(self.status('/admin'), (303, '/login'))
        for path in ['/api/live', '/api/history', '/api/home/latest', '/api/ewelink/latest', '/auth/state', '/nothing']:
            self.assertEqual(self.status(path)[0], 401, path)
        for path in ['/login', '/enrol?t=x', '/auth.js', '/health', '/version', '/favicon.svg', '/chart.umd.min.js']:
            self.assertEqual(self.status(path)[0], 200, path)
        self.assertEqual(self.post('/auth/links', {'user': 'dad'})[0], 401)

    def test_broken_settings_keep_the_site_locked(self):
        self.settings.write_text('{not json')
        self.assertEqual(self.status('/'), (303, '/login'))
        self.assertEqual(self.post('/auth/passkey/options', {})[0], 503)
        self.enable(origin='http://insecure.test')
        self.assertEqual(self.status('/api/live')[0], 401)

    def test_google_sign_in_for_allowlisted_accounts_only(self):
        self.enable()
        location, cookie = self.google('GARETH@example.test')
        self.assertEqual(location, '/'); self.assertTrue(cookie)
        self.assertEqual(self.status('/', cookie=cookie)[0], 200)
        self.assertEqual(self.status('/login', cookie=cookie), (303, '/'))
        with self.request('/auth/state', cookie=cookie) as r:
            state = json.load(r)
        self.assertEqual(state['me'], 'gareth'); self.assertEqual(state['sessions'][0]['device'], 'iPhone')
        self.assertTrue(state['sessions'][0]['current']); self.assertNotIn('dad@example.test', json.dumps(state))
        self.assertEqual(self.google('stranger@example.test'), ('/login?e=denied', None))
        self.assertEqual(self.google(aud='someone-else'), ('/login?e=denied', None))
        self.assertEqual(self.google(email_verified=False), ('/login?e=denied', None))
        self.assertEqual(self.google(exp=self.now[0] - 1), ('/login?e=denied', None))
        with self.request('/auth/google/callback?state=forged&code=c', cookie='__Host-modpi-google=forged') as r:
            self.assertEqual(r.headers['Location'], '/login?e=failed')

    def test_google_needs_the_browser_that_started_it(self):
        self.enable()
        with self.request('/auth/google/start') as r:
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(r.headers['Location']).query)
        with self.request('/auth/google/callback?' + urllib.parse.urlencode({'state': q['state'][0], 'code': 'c'})) as r:
            self.assertEqual(r.headers['Location'], '/login?e=failed')
        self.enable(google=None)
        self.assertEqual(self.status('/auth/google/start'), (303, '/login?e=nogoogle'))

    def test_sessions_renew_expire_and_sign_out(self):
        self.enable(); _, cookie = self.google()
        self.now[0] += auth.SESSION_SECONDS - 10
        with self.request('/', cookie=cookie) as r:
            self.assertEqual(r.status, 200); self.assertIn('Max-Age=%d' % auth.COOKIE_SECONDS, r.headers['Set-Cookie'])
        self.now[0] += auth.SESSION_SECONDS - 10
        self.assertEqual(self.status('/', cookie=cookie)[0], 200)
        self.assertEqual(self.post('/auth/logout', {}, cookie, origin='https://evil.test')[0], 403)
        status, _, cleared = self.post('/auth/logout', {}, cookie)
        self.assertEqual((status, cleared), (200, ''))
        self.assertEqual(self.status('/', cookie=cookie), (303, '/login'))
        _, cookie = self.google(); self.now[0] += auth.SESSION_SECONDS + 1
        self.assertEqual(self.status('/', cookie=cookie), (303, '/login'))

    def test_removed_user_and_removed_session_lose_access(self):
        self.enable(); _, mine = self.google(); _, dads = self.google('dad@example.test')
        with self.request('/auth/state', cookie=mine) as r:
            dad_session = next(s['id'] for s in json.load(r)['sessions'] if s['user'] == 'dad')
        self.assertEqual(self.post('/auth/sessions/delete', {'id': dad_session}, mine)[0], 200)
        self.assertEqual(self.status('/', cookie=dads), (303, '/login'))
        self.enable(users={'dad': {'email': 'dad@example.test'}})
        self.assertEqual(self.status('/', cookie=mine), (303, '/login'))

    @unittest.skipUnless(CRYPTO, 'python3-cryptography not installed')
    def test_sign_up_link_adds_a_passkey_once_then_passkey_signs_in(self):
        self.enable(); _, mine = self.google()
        status, link, _ = self.post('/auth/links', {'user': 'dad'}, mine)
        self.assertEqual((status, link['for']), (200, 'Dad')); self.assertTrue(link['url'].startswith(ORIGIN + '/enrol?t='))
        token = urllib.parse.parse_qs(urllib.parse.urlsplit(link['url']).query)['t'][0]
        self.assertEqual(self.post('/auth/passkey/register-options', {})[0], 401)
        status, options, _ = self.post('/auth/passkey/register-options', {'link': token})
        self.assertEqual(options['publicKey']['user']['displayName'], 'Dad')
        phone = Phone()
        status, _, dads = self.post('/auth/passkey/register', phone.create(options))
        self.assertEqual(status, 200); self.assertTrue(dads)
        self.assertEqual(self.status('/', cookie=dads)[0], 200)
        self.assertEqual(self.post('/auth/passkey/register-options', {'link': token})[0], 403)  # used up
        # Sign in again with the passkey alone.
        _, options, _ = self.post('/auth/passkey/options', {})
        status, _, again = self.post('/auth/passkey/login', phone.get(options))
        self.assertEqual(status, 200); self.assertEqual(self.status('/', cookie=again)[0], 200)
        with self.request('/auth/state', cookie=again) as r:
            state = json.load(r)
        self.assertEqual(state['me'], 'dad'); self.assertEqual([p['user'] for p in state['passkeys']], ['dad'])

    @unittest.skipUnless(CRYPTO, 'python3-cryptography not installed')
    def test_passkey_checks_refuse_tampering(self):
        self.enable(); _, mine = self.google(); phone = Phone()
        _, options, _ = self.post('/auth/passkey/register-options', {}, mine)
        self.assertEqual(self.post('/auth/passkey/register', phone.create(options, origin='https://evil.test'), mine)[0], 403)
        _, options, _ = self.post('/auth/passkey/register-options', {}, mine)
        self.assertEqual(self.post('/auth/passkey/register', phone.create(options), mine)[:2], (200, {'ok': True}))
        def attempt(**kw):
            _, options, _ = self.post('/auth/passkey/options', {})
            body = phone.get(options, **{k: v for k, v in kw.items() if k in ('origin', 'count')})
            if kw.get('tamper'): body['signature'] = auth.b64e(b'\x30\x06\x02\x01\x01\x02\x01\x01')
            if kw.get('replay'): body['challengeId'] = 'old'
            return self.post('/auth/passkey/login', body)[0]
        self.assertEqual(attempt(origin='https://evil.test'), 403)
        self.assertEqual(attempt(tamper=True), 403)
        self.assertEqual(attempt(replay=True), 400)
        self.assertEqual(attempt(count=5), 200)
        self.assertEqual(attempt(count=3), 403)  # counter went backwards: cloned key
        with self.request('/auth/state', cookie=mine) as r:
            passkey = json.load(r)['passkeys'][0]['id']
        self.assertEqual(self.post('/auth/passkeys/delete', {'id': passkey}, mine)[0], 200)
        self.assertEqual(attempt(count=9), 403)

    @unittest.skipUnless(CRYPTO, 'python3-cryptography not installed')
    def test_sign_up_link_expires(self):
        self.enable(); _, mine = self.google()
        _, link, _ = self.post('/auth/links', {'user': 'gareth'}, mine)
        token = link['url'].split('t=')[1]; self.now[0] += auth.LINK_SECONDS + 1
        self.assertEqual(self.post('/auth/passkey/register-options', {'link': token})[0], 403)
        self.assertEqual(self.post('/auth/links', {'user': 'nobody'}, mine)[0], 400)

class CborTests(unittest.TestCase):
    def test_decodes_and_rejects_bad_input(self):
        value = {1: 2, -1: b'xy', 'fmt': 'none', 'list': {'n': -300}}
        self.assertEqual(auth.cbor(cbor_encode(value))[0], value)
        for bad in [b'', b'\x5f', b'\x42a', b'\xf9\x00\x00', b'\xa1' * 20 + b'\x00']:
            with self.assertRaises(auth.AuthError): auth.cbor(bad)

if __name__ == '__main__': unittest.main()

class BondsProxyTests(AuthHttpBase):
    """The Premium Bond Tracker is served at /bonds/ to signed-in users only, with its links rewritten."""
    def setUp(self):
        import http.server
        seen = self.seen = []
        class Upstream(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self):
                seen.append(('GET', self.path, self.headers.get('Cookie')))
                body = b'<a href="/">Overview</a><a href="/people">People</a><form action="/bonds/add" method="post"></form>'
                self.send_response(200); self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
            def do_POST(self):
                seen.append(('POST', self.path, self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(303); self.send_header('Location', '/bonds?ok=1'); self.send_header('Content-Length', '0'); self.end_headers()
        self.upstream = http.server.HTTPServer(('127.0.0.1', 0), Upstream)
        threading.Thread(target=self.upstream.serve_forever, daemon=True).start()
        self.bonds_port = self.upstream.server_port
        super().setUp()
    def tearDown(self):
        super().tearDown(); self.upstream.shutdown(); self.upstream.server_close()

    def test_bonds_only_for_signed_in_users(self):
        self.assertEqual(self.status('/bonds/')[0], 404)              # login off: never public
        self.enable()
        self.assertEqual(self.status('/bonds/'), (303, '/login'))
        self.assertEqual(self.status('/bonds/people'), (303, '/login'))
        self.assertEqual(self.post('/bonds/bonds/add', {'x': 1})[0], 303)
        self.assertEqual(self.seen, [])
        _, cookie = self.google()
        with self.request('/bonds/people', cookie=cookie) as r:
            self.assertEqual(r.status, 200); self.assertEqual(r.headers['Cache-Control'], 'private, no-store')
            self.assertEqual(r.read(), b'<a href="/bonds/">Overview</a><a href="/bonds/people">People</a><form action="/bonds/bonds/add" method="post"></form>')
        self.assertEqual(self.seen[-1], ('GET', '/people', None))    # session cookie not passed on
        self.assertIn(b'<a class="modpi-back" href="/">', monitor.rewrite_bonds_html(b'<header><nav><a href="/x">x</a></nav></header>'))
        self.assertIn(b'href="/bonds/x"', monitor.rewrite_bonds_html(b'<header><a href="/x">x</a></header>'))
        with self.request('/bonds', cookie=cookie) as r:
            self.assertEqual(r.status, 200)
        self.assertEqual(self.seen[-1][1], '/')
        with self.request('/auth/state', cookie=cookie) as r:
            self.assertTrue(json.load(r)['bonds'])
        self.assertEqual(self.post('/bonds/bonds/add', {'n': 1}, cookie, origin='https://evil.test')[0], 403)
        with self.request('/bonds/bonds/add', 'POST', {'n': 1}, cookie) as r:
            self.assertEqual((r.status, r.headers['Location']), (303, '/bonds/bonds?ok=1'))
        self.assertEqual(self.seen[-1], ('POST', '/bonds/add', b'{"n": 1}'))
        self.upstream.shutdown(); self.upstream.server_close()
        self.assertEqual(self.status('/bonds/', cookie=cookie)[0], 502)
