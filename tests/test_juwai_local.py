import hashlib
import hmac
import json
import os
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import binascii

sys.path.insert(0, str(Path(__file__).parents[1] / 'router'))
sys.path.insert(0, str(Path(__file__).parent))
import juwai_local as J
import tuya_collector as collector
import monitor
from test_auth import AuthHttpBase

try:
    import cryptography  # noqa: F401
    CRYPTO = True
except ImportError:
    CRYPTO = False

KEY = '0123456789abcdef'
DEVICE = 'x' * 22
DPS_REPLY = {'18': -78, '19': -194, '20': 2491, '126': 248077}


class FakeMeter:
    """A pretend meter on localhost speaking 3.3, 3.4 or 3.5, written separately from the client."""
    def __init__(self, version, key=KEY, dps=DPS_REPLY):
        self.version, self.key, self.session, self.dps = version, key.encode(), key.encode(), dps
        self.commands = []
        self.server = socket.socket(); self.server.bind(('127.0.0.1', 0)); self.server.listen(1)
        self.port = self.server.getsockname()[1]
        self.thread = threading.Thread(target=self.serve, daemon=True); self.thread.start()

    def connect(self, _address, timeout):
        return socket.create_connection(('127.0.0.1', self.port), timeout)

    def read(self, conn, n):
        data = b''
        while len(data) < n:
            chunk = conn.recv(n - len(data))
            if not chunk: raise EOFError
            data += chunk
        return data

    def receive(self, conn):
        if self.version == '3.5':
            head = self.read(conn, 18); _, _, seq, cmd, length = struct.unpack('>IHIII', head)
            body = self.read(conn, length + 4)[:-4]
            return seq, cmd, J._gcm(self.session).decrypt(body[:12], body[12:], head[4:])
        head = self.read(conn, 16); _, seq, cmd, length = struct.unpack('>4I', head)
        tail = 36 if self.version == '3.4' else 8
        body = self.read(conn, length)
        if self.version == '3.4':
            assert hmac.compare_digest(body[-36:-4], hmac.new(self.session, head + body[:-36], hashlib.sha256).digest())
        return seq, cmd, J._unpad(J._ecb(self.session, body[:-tail], False))

    def reply(self, conn, seq, cmd, plain):
        if self.version == '3.5':
            nonce = os.urandom(12); plain = b'\x00\x00\x00\x00' + plain
            head = struct.pack('>IHIII', 0x6699, 0, seq, cmd, len(plain) + 28)
            return conn.sendall(head + nonce + J._gcm(self.session).encrypt(nonce, plain, head[4:]) + struct.pack('>I', 0x9966))
        body = b'\x00\x00\x00\x00' + J._ecb(self.session, J._pad(plain), True)
        frame = struct.pack('>4I', 0x55AA, seq, cmd, len(body) + (36 if self.version == '3.4' else 8)) + body
        frame += hmac.new(self.session, frame, hashlib.sha256).digest() if self.version == '3.4' \
            else struct.pack('>I', binascii.crc32(frame) & 0xFFFFFFFF)
        conn.sendall(frame + struct.pack('>I', 0xAA55))

    def serve(self):
        try:
            conn, _ = self.server.accept()
        except OSError:
            return
        with conn:
            try:
                while True:
                    seq, cmd, plain = self.receive(conn)
                    self.commands.append(cmd)
                    if cmd == J.SESS_START:
                        remote = os.urandom(16)
                        self.reply(conn, seq, J.SESS_RESP, remote + hmac.new(self.key, plain, hashlib.sha256).digest())
                        _, cmd, proof = self.receive(conn); self.commands.append(cmd)
                        assert proof == hmac.new(self.key, remote, hashlib.sha256).digest()
                        mixed = bytes(a ^ b for a, b in zip(plain, remote))
                        self.session = J._ecb(self.key, mixed, True) if self.version == '3.4' \
                            else J._gcm(self.key).encrypt(plain[:12], mixed, None)[:16]
                    else:
                        assert json.loads(plain)['devId'] == DEVICE
                        self.reply(conn, seq, cmd, json.dumps({'dps': self.dps}).encode())
            except Exception:
                pass  # wrong key or closed connection: hang up, as a real meter does

    def close(self):
        self.server.close()


@unittest.skipUnless(CRYPTO, 'python3-cryptography not installed (it is on the Pi)')
class ProtocolTests(unittest.TestCase):
    def read(self, version, key=KEY):
        fake = FakeMeter(version)
        self.addCleanup(fake.close)
        device = {'host': '192.0.2.1', 'id': DEVICE, 'key': key, 'version': version}
        return fake, J.read_meter(device, connect=fake.connect)

    def test_all_three_versions_read_status_with_cloud_scaling_and_only_query_commands(self):
        for version in J.VERSIONS:
            fake, (output, raw) = self.read(version)
            self.assertEqual(output, {'voltage_v': 24.91, 'current_a': -0.078, 'power_w': -1.94})
            self.assertEqual(raw['126'], 248077)  # extra data points are kept raw, not interpreted
            allowed = {J.DP_QUERY} if version == '3.3' else {J.SESS_START, J.SESS_FINISH, J.DP_QUERY_NEW}
            self.assertEqual(set(fake.commands), allowed)

    def test_wrong_key_is_a_clean_failure_that_names_no_secret(self):
        for version in J.VERSIONS:
            with self.assertRaises(J.LocalError) as error:
                self.read(version, key='f' * 16)
            for secret in (KEY, DEVICE, '192.0.2.1', 'f' * 16):
                self.assertNotIn(secret, str(error.exception))

    def test_unreachable_meter_is_a_clean_failure(self):
        def refuse(*_): raise ConnectionRefusedError()
        with self.assertRaises(J.LocalError):
            J.read_meter({'host': '192.0.2.1', 'id': DEVICE, 'key': KEY, 'version': '3.5'}, connect=refuse)


class DecodeTests(unittest.TestCase):
    def test_missing_and_invalid_values_stay_missing(self):
        output, _ = J.decode_dps({'20': 2625, '18': True, '101': False})
        self.assertEqual(output, {'voltage_v': 26.25, 'current_a': None, 'power_w': None})
        for bad in ({'101': False}, {'x': 1, '20': 1}, [], {'20': float('nan')}):
            with self.assertRaises(ValueError):
                J.decode_dps(bad)

    def test_other_meter_models_can_map_their_own_data_points(self):
        mapping = J.parse_dps_map({'power_w': ['101', 100], 'voltage_v': ['102', 100], 'current_a': ['103', 1000]})
        output, _ = J.decode_dps({'101': 2129, '102': 3575, '103': 595}, mapping)
        self.assertEqual(output, {'voltage_v': 35.75, 'current_a': 0.595, 'power_w': 21.29})
        for bad in ({}, {'power_w': ['x', 100]}, {'power_w': ['19', 0]}, {'power_w': ['19', True]},
                    {'resistance': ['19', 100]}, {'power_w': ['19']}, []):
            with self.assertRaises(ValueError):
                J.parse_dps_map(bad)

    def test_scan_refuses_public_or_large_networks(self):
        for network in ('8.8.8.0/24', '10.0.0.0/16'):
            with self.assertRaises(ValueError):
                J.open_hosts(network)


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.store = collector.USBStore(self.root / 'juwai_local.sqlite3', lambda: None)
        self.store.db.executescript(J.EXTRA_SCHEMA)
        self.config = self.root / 'juwai-local.json'
        self.config.write_text(json.dumps({'devices': {'shed': {'host': '192.168.0.2', 'version': '3.5'},
                                                       'garage': {'host': '192.168.0.3', 'version': '3.5'}}}))
        self.conf = {'interval': 60, 'network': '192.168.0.0/24', 'path': self.config, 'devices': {
            'shed': {'id': DEVICE, 'key': KEY, 'host': '192.168.0.2', 'version': '3.5'},
            'garage': {'id': DEVICE, 'key': KEY, 'host': '192.168.0.3', 'version': '3.5'}}}

    def tearDown(self):
        self.store.close(); self.tmp.cleanup()

    def cycles(self, count, read, find=lambda *_: None):
        stop = threading.Event(); seen = [0]
        def counted(device):
            seen[0] += 1
            if seen[0] >= count * 2: stop.set()
            return read(device)
        stop.wait = lambda _t: None
        J.run(self.conf, self.store, stop, read=counted, find=find)

    def test_failed_reads_leave_gaps_are_counted_and_do_not_stop_the_other_meter(self):
        def read(device):
            if device['host'].endswith('.3'): raise J.LocalError('Meter unreachable')
            return {'voltage_v': 26.25, 'current_a': .997, 'power_w': 26.19}, {'20': 2625}
        self.cycles(3, read)
        rows = dict(self.store.db.execute('SELECT device,COUNT(*) FROM samples GROUP BY device').fetchall())
        self.assertEqual(rows, {'shed': rows['shed']})  # nothing invented for the garage
        stats = {r[0]: r[1:] for r in self.store.db.execute('SELECT device,ok,failed FROM read_stats')}
        self.assertEqual(stats['garage'][0], 0); self.assertGreaterEqual(stats['garage'][1], 2)
        self.assertEqual(stats['shed'][1], 0)

    def test_new_address_is_found_recorded_privately_and_logged_as_an_event(self):
        def read(device):
            if device['host'] == '192.168.0.3': raise J.LocalError('Meter unreachable')
            return {'voltage_v': 24.0, 'current_a': 0.0, 'power_w': 0.0}, {'20': 2400}
        searches = []
        def find(device, network):
            searches.append(network); return '192.168.0.99', '3.5'
        self.cycles(8, read, find)
        self.assertEqual(len(searches), 1)  # found on the first search, so no repeat
        saved = json.loads(self.config.read_text())
        self.assertEqual(saved['devices']['garage']['host'], '192.168.0.99')
        self.assertEqual(os.stat(self.config).st_mode & 0o777, 0o600)
        kinds = [r[0] for r in self.store.db.execute("SELECT kind FROM events WHERE device='garage'")]
        self.assertIn('address_changed', kinds)
        self.assertNotIn('192.168', json.dumps(self.store.db.execute('SELECT * FROM events').fetchall()))

    def test_missing_meter_is_searched_for_every_few_minutes_not_every_cycle(self):
        clock = [1000.0]; searches = []
        def read(device):
            if device['host'].endswith('.3'): raise J.LocalError('Meter unreachable')
            clock[0] += 60  # one cycle a minute (the shed is read each cycle)
            return {'voltage_v': 24.0, 'current_a': 0.0, 'power_w': 0.0}, {'20': 2400}
        def find(device, network):
            searches.append(clock[0]); return None
        with mock.patch.object(J.time, 'monotonic', lambda: clock[0]):
            self.cycles(24, read, find)
        self.assertGreaterEqual(len(searches), 3)  # repeats while the meter stays missing
        self.assertLessEqual(len(searches), 5)  # but not every cycle
        self.assertTrue(all(b - a >= J.SEARCH_INTERVAL for a, b in zip(searches, searches[1:])))
        self.assertLessEqual(J.SEARCH_INTERVAL, 300)

    def test_comparison_pairs_cloud_samples_with_nearby_local_ones_only(self):
        cloud = collector.USBStore(self.root / 'tuya.sqlite3', lambda: None)
        now = int(time.time() * 1000)
        values = lambda p: {'voltage_v': 26.0, 'current_a': 1.0, 'power_w': p}
        cloud.add('shed', now - 600000, values(26.0), {})
        cloud.add('shed', now - 300000, values(27.0), {})   # no local sample near this one
        cloud.add('shed', now - 1000, values(28.0), {})     # too recent: wait for a closer local sample
        self.store.add('shed', now - 590000, values(26.5), {})
        uri = (self.root / 'tuya.sqlite3').as_uri() + '?mode=ro'
        for _ in range(2):
            J.compare_with_cloud(self.store, uri, ['shed'])
        rows = self.store.db.execute('SELECT cloud_ts,cloud_power_w,local_power_w FROM compare').fetchall()
        self.assertEqual(rows, [(now - 600000, 26.0, 26.5)])
        cloud.close()
        J.compare_with_cloud(self.store, 'file:/nonexistent/tuya.sqlite3?mode=ro', ['shed'])  # must not raise

    def test_config_must_be_private_and_complete(self):
        keys = self.root / 'tuya-local-keys.json'
        keys.write_text(json.dumps({'devices': {'shed': {'id': DEVICE, 'local_key': KEY}}}))
        self.config.write_text(json.dumps({'interval_seconds': 60, 'devices': {'shed': {'host': '192.168.0.2', 'version': '3.5'}}}))
        os.chmod(keys, 0o600); os.chmod(self.config, 0o644)
        with self.assertRaises(RuntimeError):
            J.load_config(self.config)
        os.chmod(self.config, 0o600)
        self.assertEqual(J.load_config(self.config)['devices']['shed']['key'], KEY)
        keys.write_text(json.dumps({'devices': {'p275': {'id': DEVICE, 'local_key': KEY}}}))
        self.config.write_text(json.dumps({'devices': {'p275': {'host': '192.168.0.3', 'version': '3.4',
                                                                  'dps': {'power_w': ['19', 100]}}}}))
        os.chmod(self.config, 0o600)
        self.assertEqual(J.load_config(self.config)['devices']['p275']['dps'], {'power_w': ('19', 100.0)})
        keys.write_text(json.dumps({'devices': {'shed': {'id': DEVICE, 'local_key': KEY}}}))
        self.config.write_text(json.dumps({'interval_seconds': 60, 'devices': {'shed': {'host': '192.168.0.2', 'version': '3.5'}}}))
        os.chmod(self.config, 0o600)
        self.config.write_text(json.dumps({'interval_seconds': 5, 'devices': {'shed': {'host': '192.168.0.2', 'version': '3.5'}}}))
        os.chmod(self.config, 0o600)
        with self.assertRaises(ValueError):
            J.load_config(self.config)


class SourceSwitchHttpTests(AuthHttpBase):
    """One setting picks the source for everyone; only signed-in users may look at the other one."""
    def serve(self, juwai_source='cloud'):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        root = Path(self.tmp.name)
        import contextlib
        @contextlib.contextmanager
        def directory():
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try: yield fd
            finally: os.close(fd)
        now = int(time.time() * 1000)
        for name, power, age in (('tuya', 10.0, 600000), ('juwai_local', 20.0, 600000)):
            writer = collector.USBStore(root / (name + '.sqlite3'), lambda: None)
            writer.add('shed', now - age, {'voltage_v': 26.0, 'current_a': 1.0, 'power_w': power}, {'secret_raw': 1})
            writer.close()
        uri = lambda name: (lambda _fd: (root / name).as_uri() + '?mode=ro')
        class Store:
            def live(self, *a, **kw): return {'stale': False, 'latest': {'ts': 1}}
        self.server = monitor.WebServer(('127.0.0.1', 0), monitor.handler(
            Store(), Path(monitor.__file__).parent / 'web', auth=self.auth,
            tuya=J.CachedTuya(directory, lambda: None, database_uri=uri('tuya.sqlite3')),
            juwai_local=J.CachedLocal(directory, lambda: None, database_uri=uri('juwai_local.sqlite3')),
            juwai_source=juwai_source))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)

    def json(self, path, cookie=None):
        with self.request(path, cookie=cookie) as r:
            return r.status, json.loads(r.read() or b'{}')

    def test_cloud_is_the_default_and_the_switch_needs_a_signed_in_user(self):
        self.serve()
        status, data = self.json('/api/tuya/latest')           # login off: nobody is signed in
        self.assertEqual((status, data['source'], data['switchable']), (200, 'cloud', False))
        self.assertEqual(self.json('/api/tuya/latest?source=local')[0], 400)
        self.enable()
        _, cookie = self.google()
        status, data = self.json('/api/tuya/latest', cookie)
        self.assertEqual((data['source'], data['switchable'], data['devices'][0]['values']['power_w']), ('cloud', True, 10.0))
        self.assertFalse(data['devices'][0]['stale'])           # 10 minutes old is fine for 15-minute cloud reads
        status, data = self.json('/api/tuya/latest?source=local', cookie)
        self.assertEqual((data['source'], data['devices'][0]['values']['power_w']), ('local', 20.0))
        self.assertTrue(data['devices'][0]['stale'])            # but stale for 1-minute local reads, and labelled so
        self.assertIsNone(data['devices'][1]['values']['power_w'])  # no garage sample: nothing made up
        self.assertNotIn('secret_raw', json.dumps(data))
        status, data = self.json('/api/tuya/history?device=shed&window=24h&source=local', cookie)
        self.assertEqual((status, data['source']), (200, 'local'))
        for bad in ('?source=solar', '?source=local&source=cloud', '?source=local&x=1'):
            self.assertEqual(self.json('/api/tuya/latest' + bad, cookie)[0], 400)

    def test_one_setting_switches_the_source_for_everyone(self):
        self.serve('local')
        status, data = self.json('/api/tuya/latest')
        self.assertEqual((status, data['source'], data['devices'][0]['values']['power_w']), (200, 'local', 20.0))
        self.assertEqual(self.json('/api/tuya/history?device=shed&window=24h')[1]['source'], 'local')


if __name__ == '__main__':
    unittest.main()
