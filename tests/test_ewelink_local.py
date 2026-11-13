import contextlib
import json
import os
from pathlib import Path
import sqlite3
import struct
import sys
import tempfile
import time
import unittest

import ewelink as e
import ewelink_local as L

try:
    import cryptography  # noqa: F401
    CRYPTO = True
except ImportError:
    CRYPTO = False

KEY = 'f00dfeed-0000-4000-8000-123456789abc'


def _labels(name):
    return b''.join(bytes([len(p)]) + p.encode() for p in name.split('.')) + b'\0'


def _rr(name, rtype, rdata, cls=0x8001, ttl=120):
    return _labels(name) + struct.pack('!2HIH', rtype, cls, ttl, len(rdata)) + rdata


def announcement(deviceid, txt, ip='10.0.0.5', port=8081, compress=False):
    """A response like a plug sends: PTR, SRV, TXT and A records."""
    inst = 'eWeLink_%s._ewelink._tcp.local' % deviceid
    host = 'eWeLink_%s.local' % deviceid
    txt_raw = b''.join(bytes([len(s)]) + s for s in (('%s=%s' % kv).encode() for kv in txt.items()))
    records = [_rr('_ewelink._tcp.local', L.T_PTR, _labels(inst)),
               _rr(inst, L.T_SRV, struct.pack('!3H', 0, 0, port) + _labels(host)),
               _rr(inst, L.T_TXT, txt_raw),
               _rr(host, L.T_A, bytes(int(x) for x in ip.split('.')))]
    packet = struct.pack('!6H', 0, 0x8400, 0, len(records), 0, 0) + b''.join(records)
    if compress:
        # Point the TXT record's owner name at the SRV record's owner name.
        first = packet.index(_labels(inst))
        second = packet.index(_labels(inst), first + 1)
        packet = packet[:second] + struct.pack('!H', 0xC000 | first) + packet[second + len(_labels(inst)):]
    return packet


def plain_txt(params, seq='1'):
    raw = json.dumps(params)
    return {'txtvers': '1', 'id': 'x', 'type': 'plug', 'apivers': '1', 'seq': seq,
            'data1': raw[:200], 'data2': raw[200:]}


class Clock:
    def __init__(self):self.t = 1000.0
    def __call__(self):return self.t


class ProtocolTests(unittest.TestCase):
    def test_query_is_one_ptr_question(self):
        q = L.mdns_query()
        self.assertEqual(struct.unpack_from('!6H', q), (0, 0, 1, 0, 0, 0))
        self.assertTrue(q.endswith(struct.pack('!2H', 12, 1)))
        self.assertIn(b'\x08_ewelink\x04_tcp\x05local\x00', q)

    def test_parse_announcement(self):
        for compress in (False, True):
            ann = L.Announcements()
            out = ann.feed(announcement('1000abcdef', plain_txt({'power': '12.5'}), compress=compress))
            self.assertEqual(len(out), 1)
            did, ip, port, txt = out[0]
            self.assertEqual((did, ip, port), ('1000abcdef', '10.0.0.5', 8081))
            self.assertEqual(L.txt_params(txt, KEY), {'power': '12.5'})

    def test_queries_and_other_kit_ignored(self):
        self.assertEqual(L.parse_mdns(L.mdns_query()), [])
        self.assertIsNone(L.device_id('zbbridgeu-1000abc._ewelink._tcp.local'))
        self.assertIsNone(L.device_id('eWeLink_1000abc._other._tcp.local'))
        self.assertEqual(L.parse_mdns(b'\x00' * 5), [])

    def test_truncated_packet_keeps_earlier_records(self):
        packet = announcement('1000abcdef', plain_txt({'power': '1'}))
        self.assertTrue(L.parse_mdns(packet[:-6]))

    def test_only_state_reads_can_be_sent(self):
        for command in ('switch', 'switches', 'uiActive', 'startup', 'pulse', 'sledonline', ''):
            with self.assertRaises(ValueError):
                L.read_state('10.0.0.5', 8081, 'id', KEY, command)

    def test_no_control_vocabulary(self):
        source = Path(L.__file__).read_text()
        self.assertNotIn('/zeroconf/switch', source)
        self.assertNotIn('uiActive', source.replace('uiActive_', ''))

    @unittest.skipUnless(CRYPTO, 'python3-cryptography not installed (it is on the Pi)')
    def test_encrypt_round_trip_and_wrong_key(self):
        data, iv = L.encrypt({'power': '1234', 'voltage': '23456'}, KEY)
        self.assertEqual(L.decrypt(data, iv, KEY), {'power': '1234', 'voltage': '23456'})
        with self.assertRaises(L.LocalError):
            L.decrypt(data, iv, 'another-key')

    @unittest.skipUnless(CRYPTO, 'python3-cryptography not installed (it is on the Pi)')
    def test_encrypted_txt(self):
        data, iv = L.encrypt({'power': '440'}, KEY)
        txt = {'encrypt': 'true', 'iv': iv, 'data1': data[:249], 'data2': data[249:]}
        self.assertEqual(L.txt_params(txt, KEY), {'power': '440'})

    @unittest.skipUnless(CRYPTO, 'python3-cryptography not installed (it is on the Pi)')
    def test_read_state_sends_empty_encrypted_body(self):
        sent = []

        class Reply:
            def __enter__(self):return self
            def __exit__(self, *a):pass
            def read(self, n):
                data, iv = L.encrypt({'power': '5'}, KEY)
                return json.dumps({'error': 0, 'seq': 3, 'iv': iv, 'data': data}).encode()

        def opener(req, timeout):
            sent.append(req)
            return Reply()
        self.assertEqual(L.read_state('10.0.0.5', 8081, 'dev', KEY, 'getState', opener=opener), {'power': '5'})
        body = json.loads(sent[0].data)
        self.assertTrue(sent[0].full_url.endswith(':8081/zeroconf/getState'))
        self.assertEqual(L.decrypt(body['data'], body['iv'], KEY), {})


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cloud = e.Ewelink(None, lambda: None, self.root / 'config.json')

        @contextlib.contextmanager
        def database(readonly=False):
            path = str(self.root / 'ewelink.sqlite3')
            db = sqlite3.connect('file:' + path + '?mode=ro' if readonly else path, uri=readonly)
            try:
                if not readonly:
                    db.executescript(e.SCHEMA)
                yield db
            finally:
                db.close()
        self.cloud.database = database
        self.keys = self.root / 'ewelink-local.json'
        e.atomic_json(self.keys, {'devices': {
            'inverter-1000': {'id': '1000aaaaaa', 'devicekey': KEY, 'uiid': 190},
            'inverter-600': {'id': '1000bbbbbb', 'devicekey': KEY, 'uiid': 190},
            'garage-inverter': {'id': '1000dddddd', 'devicekey': KEY, 'uiid': 32}}})
        self.clock, self.wall = Clock(), Clock()
        self.wall.t = 1_700_000_000.0
        self.local = L.EwelinkLocal(self.cloud, self.keys, clock=self.clock, wall=self.wall)
        self.local.load()

    def tearDown(self):
        self.temp.cleanup()

    def advance(self, seconds):
        self.clock.t += seconds
        self.wall.t += seconds

    def rows(self, table, device):
        with self.cloud.database(readonly=True) as db:
            return [(ts, json.loads(p)) for ts, p in
                    db.execute('SELECT ts,payload FROM %s WHERE device=? ORDER BY ts' % table, (device,))]

    def test_load_keys_rejects_unknown_slug(self):
        e.atomic_json(self.keys, {'devices': {'kettle': {'id': 'x', 'devicekey': 'y'}}})
        with self.assertRaises(RuntimeError):
            L.load_keys(self.keys)

    def test_uiid_scaling_and_latest_marked_local(self):
        self.local.handle(announcement('1000aaaaaa', plain_txt({'power': '4424', 'voltage': '23559', 'current': '27',
                                                                 'switches': [{'switch': 'on', 'outlet': 0}]})))
        self.local.handle(announcement('1000bbbbbb', plain_txt({'power': '1250', 'voltage': '23790', 'current': '5', 'switch': 'on'})))
        self.local.tick()
        (_, big), = self.rows('latest', 'inverter-1000')
        self.assertEqual(big, {'power_w': 44.24, 'voltage_v': 235.59, 'current_a': 0.27, 'switch': 'on',
                               'online': True, 'source': 'local'})
        (_, small), = self.rows('latest', 'inverter-600')
        self.assertEqual(small['power_w'], 12.5)
        self.assertEqual(small['source'], 'local')
        self.assertTrue(self.local.fresh('inverter-1000'))
        self.assertFalse(self.local.fresh('fridge'))

    def test_uiid_32_left_to_the_cloud(self):
        # These plugs repeat the state they last pushed, which can be hours old.
        self.assertEqual(self.local.handle(announcement('1000dddddd', plain_txt({'power': '1.5'}))), [])
        self.assertFalse(self.local.fresh('garage-inverter'))

    def test_unknown_device_and_undecodable_ignored(self):
        self.assertEqual(self.local.handle(announcement('1000cccccc', plain_txt({'power': '1'}))), [])
        bad = {'data1': '{not json', 'seq': '1'}
        self.assertEqual(self.local.handle(announcement('1000aaaaaa', bad)), [])
        self.assertEqual(self.local.handle(announcement('1000aaaaaa', plain_txt({'switch': 'on'}))), [])

    def test_history_sample_is_time_weighted(self):
        send = lambda w: self.local.handle(announcement('1000bbbbbb', plain_txt({'power': str(w * 100)})))
        send(100)
        self.local.tick()
        self.advance(10); send(400)
        self.advance(20); self.local.tick()  # 30 s window: 10 s at 100 W, 20 s at 400 W
        (_, sample), = self.rows('samples', 'inverter-600')
        self.assertAlmostEqual(sample['power_w'], 300.0)
        self.assertEqual(sample['source'], 'local')
        (_, latest), = self.rows('latest', 'inverter-600')
        self.assertEqual(latest['power_w'], 400.0)

    def test_lost_after_a_minute_then_back(self):
        self.local.handle(announcement('1000bbbbbb', plain_txt({'power': '5'})))
        self.local.tick()
        self.advance(59)
        self.assertTrue(self.local.fresh('inverter-600'))
        self.advance(2)
        self.assertFalse(self.local.fresh('inverter-600'))
        self.local.tick()
        self.local.handle(announcement('1000bbbbbb', plain_txt({'power': '6'})))
        self.local.tick()
        with self.cloud.database(readonly=True) as db:
            kinds = [r[0] for r in db.execute("SELECT kind FROM local_events WHERE device='inverter-600' ORDER BY rowid")]
        self.assertEqual(kinds, ['local_back', 'local_lost', 'local_back'])

    def test_cloud_skips_plugs_read_locally(self):
        calls = []

        def transport(region, method, path, body, headers):
            calls.append(path)
            return {'thingList': [{'itemType': 1, 'itemData': {'deviceid': d, 'online': True, 'extra': {'uiid': 32},
                                   'params': {'power': '9', 'voltage': '230', 'current': '0.1', 'switch': 'on'}}}
                                  for d in ('1000aaaaaa', '1000bbbbbb')]}
        cloud = self.cloud
        cloud.transport = transport
        cloud.last_request = -10
        e.atomic_json(cloud.config_path, dict(appid='app', secret='secret', redirect_url=e.CALLBACK, app_expires_at=time.time() + 86400,
                                              devices={'inverter-1000': {'id': '1000aaaaaa', 'item_type': 1},
                                                       'inverter-600': {'id': '1000bbbbbb', 'item_type': 1}}))
        cloud.save_tokens(dict(accessToken='a', refreshToken='r', atExpiredTime=int(time.time() * 1000) + 2 * 86400000,
                               rtExpiredTime=int(time.time() * 1000) + 3 * 86400000), 'eu')
        cloud.local_fresh = self.local.fresh
        self.local.handle(announcement('1000bbbbbb', plain_txt({'power': '500'})))
        self.local.tick()
        cloud.collect()
        self.assertEqual(len(calls), 1)
        (_, big), = self.rows('latest', 'inverter-1000')
        (_, small), = self.rows('latest', 'inverter-600')
        self.assertEqual((big['source'], big['power_w']), ('cloud', 9.0))
        self.assertEqual((small['source'], small['power_w']), ('local', 5.0))

    def test_history_counts_five_minute_slots(self):
        start = int(time.time() * 1000) // 3600000 * 3600000 - 2 * 3600000
        with self.cloud.database() as db, db:
            # Ten minutes of local readings every 30 s at 120 W, then one cloud reading at 60 W.
            for i in range(20):
                db.execute('INSERT INTO samples VALUES(?,?,?)', ('inverter-600', start + i * 30000,
                           json.dumps({'power_w': 120.0, 'online': True, 'source': 'local'})))
            db.execute('INSERT INTO samples VALUES(?,?,?)', ('inverter-600', start + 900000,
                       json.dumps({'power_w': 60.0, 'online': True, 'source': 'cloud'})))
        self.cloud.guard = lambda: None
        result = self.cloud.history('inverter-600', '24h')
        samples = sum(b['samples'] for b in result['bins'])
        wh = sum(b['mean_w'] * b['samples'] for b in result['bins'] if b['samples']) * e.INTERVAL / 3600
        self.assertEqual(samples, 3)
        self.assertAlmostEqual(wh, 120 * 10 / 60 + 60 * 5 / 60)


class RunConfigTests(unittest.TestCase):
    def test_local_flag_must_be_boolean(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pi'))
        import run
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
            json.dump({'ewelink_local_enabled': 'yes'}, f)
        try:
            with self.assertRaises(ValueError):
                run.load_config(f.name)
            Path(f.name).write_text(json.dumps({'ewelink_local_enabled': True}))
            self.assertTrue(run.load_config(f.name)['ewelink_local_enabled'])
        finally:
            os.unlink(f.name)


if __name__ == '__main__':
    unittest.main()
