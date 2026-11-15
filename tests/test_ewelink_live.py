import contextlib
import json
from pathlib import Path
import socket
import sqlite3
import struct
import tempfile
import threading
import unittest

import ewelink as e
import ewelink_live as V


class Clock:
    def __init__(self, t):self.t = t
    def __call__(self):return self.t


class FakeCloud:
    """Stands in for ewelink.Ewelink: config, token, budgeted request and the shared history."""
    def __init__(self, root, things):
        self.root, self.things, self.requests = root, things, []
        self.read_lock, self.history_cache = threading.RLock(), {'x': 1}
    @contextlib.contextmanager
    def exclusive(self):yield
    def config(self):
        return {'appid': 'APPID', 'devices': {'inverter-1000': {'id': '1000aaaaaa', 'item_type': 1},
                'garage-inverter': {'id': '1000dddddd', 'item_type': 1}, 'inverter-600': '1000bbbbbb',
                'charger-inverter': {'id': '1000cccccc', 'item_type': 1}}}
    def token(self):return {'region': 'eu', 'accessToken': 'AT'}
    def request(self, region, method, path, body=None, token=None):
        self.requests.append((method, path, body))
        return {'thingList': [{'itemType': 1, 'itemData': d} for d in self.things]}
    @contextlib.contextmanager
    def database(self, readonly=False):
        path = str(self.root / 'ewelink.sqlite3')
        db = sqlite3.connect('file:' + path + '?mode=ro' if readonly else path, uri=readonly)
        try:
            if not readonly:
                db.executescript(e.SCHEMA)
            yield db
        finally:
            db.close()


def thing(deviceid, uiid):
    return {'deviceid': deviceid, 'apikey': 'OWNER', 'extra': {'uiid': uiid}, 'params': {}}


class FakeWS:
    def __init__(self, incoming, live, stop_after):
        self.sent, self.incoming, self.live, self.stop_after, self.closed = [], list(incoming), live, stop_after, False
    def send_text(self, text):self.sent.append(text)
    def recv(self, timeout=1.0):
        self.live.clock.t += 1; self.live.wall.t += 1
        self.stop_after -= 1
        if self.stop_after <= 0:
            self.live.stop.set()
        return self.incoming.pop(0) if self.incoming else None
    def close(self):self.closed = True


class OutgoingTests(unittest.TestCase):
    def test_ui_active_is_the_only_update(self):
        good = V.ui_active_message('k', 'd', '1')
        V.check_outgoing(good)
        self.assertEqual(good['params'], {'uiActive': 60})
        for params in ({'switch': 'off'}, {'switch': 'on'}, {'uiActive': 60, 'switch': 'on'},
                       {'uiActive': 3600}, {'startup': 'off'}, {'pulse': 'on'}, {}, [], None):
            bad = dict(good, params=params)
            with self.assertRaises(ValueError):
                V.check_outgoing(bad)
        with self.assertRaises(ValueError):
            V.check_outgoing(dict(good, action='query'))
        with self.assertRaises(ValueError):
            V.check_outgoing(dict(good, extra='x'))
        with self.assertRaises(ValueError):
            V.check_outgoing({'action': 'userOnline', 'params': {'switch': 'on'}})

    def test_no_control_words_in_source(self):
        source = Path(V.__file__).read_text()
        for word in ('"switch":', "'switch':", 'startup', 'pulse', 'sledOnline', 'zeroconf'):
            self.assertNotIn(word, source)


class WebSocketTests(unittest.TestCase):
    def setUp(self):
        self.a, self.b = socket.socketpair()
        self.ws = V.WebSocket(self.a)
    def tearDown(self):
        self.a.close(); self.b.close()

    def server_frame(self, opcode, payload, fin=True):
        n = len(payload)
        head = bytes([(0x80 if fin else 0) | opcode])
        head += bytes([n]) if n < 126 else bytes([126]) + struct.pack('>H', n)
        return head + payload

    def client_frame(self):
        data = self.b.recv(65536)
        n, pos = data[1] & 0x7F, 2
        if n == 126:
            n, pos = struct.unpack('>H', data[2:4])[0], 4
        self.assertTrue(data[1] & 0x80, 'client frames must be masked')
        mask = data[pos:pos + 4]
        return data[0] & 0x0F, bytes(x ^ mask[i % 4] for i, x in enumerate(data[pos + 4:pos + 4 + n]))

    def test_send_is_masked_text(self):
        self.ws.send_text('x' * 300)
        self.assertEqual(self.client_frame(), (1, b'x' * 300))

    def test_recv_text_fragments_ping_and_timeout(self):
        self.b.sendall(self.server_frame(0x9, b'hi') + self.server_frame(0x1, b'{"a":', fin=False)
                       + self.server_frame(0x0, b'1}'))
        self.assertEqual(self.ws.recv(1), '{"a":1}')
        self.assertEqual(self.client_frame(), (0xA, b'hi'))
        self.assertIsNone(self.ws.recv(0.05))

    def test_partial_frame_waits(self):
        frame = self.server_frame(0x1, b'pong')
        self.b.sendall(frame[:3])
        self.assertIsNone(self.ws.recv(0.05))
        self.b.sendall(frame[3:])
        self.assertEqual(self.ws.recv(1), 'pong')

    def test_close_raises(self):
        self.b.sendall(self.server_frame(0x8, b''))
        with self.assertRaises(V.LiveError):
            self.ws.recv(1)


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cloud = FakeCloud(self.root, [thing('1000dddddd', 32), thing('1000bbbbbb', 32),
                                           thing('1000cccccc', 32), thing('1000aaaaaa', 190)])
        self.live = V.EwelinkLive(self.cloud, connect=None, dispatcher=lambda r, t: ('eu-pconnect3.coolkit.cc', 8080),
                                  clock=Clock(5000.0), wall=Clock(1_700_000_000.0))
    def tearDown(self):
        self.temp.cleanup()

    def rows(self, table, device):
        with self.cloud.database(readonly=True) as db:
            return [json.loads(p) for (p,) in db.execute('SELECT payload FROM %s WHERE device=? ORDER BY ts' % table, (device,))]

    def update(self, deviceid, **params):
        return json.dumps({'action': 'update', 'deviceid': deviceid, 'apikey': 'OWNER', 'userAgent': 'device', 'params': params})

    def test_setup_picks_the_powr2_plugs_with_one_call(self):
        self.live.setup()
        self.assertEqual(sorted(self.live.plugs), ['charger-inverter', 'garage-inverter', 'inverter-600'])
        self.assertEqual(len(self.cloud.requests), 1)
        self.assertEqual(self.cloud.requests[0][:2], ('POST', '/v2/device/thing'))
        self.assertNotIn('1000aaaaaa', json.dumps(self.cloud.requests[0][2]))

    def test_no_powr2_plugs_stops_without_more_calls(self):
        self.cloud.things = [thing('1000bbbbbb', 190)]
        self.live.poll()
        self.assertEqual(len(self.cloud.requests), 1)
        self.assertEqual(self.live.status, 'Not needed')

    def test_session_signs_in_asks_and_stores_live_power(self):
        self.live.setup()
        incoming = [json.dumps({'error': 0, 'apikey': 'OWNER', 'config': {'hb': 1, 'hbInterval': 145}, 'sequence': '1'}),
                    json.dumps({'error': 0, 'deviceid': '1000bbbbbb', 'sequence': '2'}),
                    self.update('1000bbbbbb', uiActive=60),
                    self.update('1000bbbbbb', power='33.50', voltage='236.10', current='0.21'),
                    self.update('1000bbbbbb', power='4.20')]
        ws = FakeWS(incoming, self.live, stop_after=40)
        self.live.connect_ws = lambda host, port: ws
        self.live.session()
        sent = [json.loads(t) for t in ws.sent if t != 'ping']
        self.assertEqual(sent[0]['action'], 'userOnline')
        self.assertEqual((sent[0]['at'], sent[0]['appid'], sent[0]['apikey']), ('AT', 'APPID', 'OWNER'))
        asks = sent[1:]
        self.assertTrue(asks)
        for m in asks:
            self.assertEqual(m['params'], {'uiActive': 60})
        self.assertEqual({m['deviceid'] for m in asks}, {'1000bbbbbb', '1000cccccc', '1000dddddd'})
        self.assertTrue(ws.closed)
        latest = self.rows('latest', 'inverter-600')
        self.assertEqual(latest[-1]['source'], 'live')
        self.assertEqual(latest[-1]['power_w'], 4.2)
        self.assertEqual(latest[-1]['voltage_v'], 236.1)   # kept from the earlier push
        self.assertTrue(self.rows('samples', 'inverter-600'))
        self.assertTrue(self.live.fresh('inverter-600'))
        self.assertFalse(self.live.fresh('garage-inverter'))
        self.assertFalse(self.live.fresh('inverter-1000'))
        self.assertEqual(len(self.cloud.requests), 1)

    def test_refused_sign_in_raises(self):
        self.live.setup()
        ws = FakeWS([json.dumps({'error': 406, 'reason': 'Authentication Failed'})], self.live, stop_after=50)
        self.live.connect_ws = lambda host, port: ws
        with self.assertRaises(V.LiveError):
            self.live.session()
        self.assertTrue(ws.closed)

    def test_switch_off_reads_zero_and_lost_plug_goes_back_to_cloud(self):
        self.live.setup()
        self.live.handle(self.update('1000bbbbbb', power='40.00', switch='on'))
        self.live.handle(self.update('1000bbbbbb', switch='off'))
        self.live.tick()
        self.assertEqual(self.rows('latest', 'inverter-600')[-1]['power_w'], 0.0)
        self.live.clock.t += V.FRESH + 1
        self.live.tick()
        self.assertFalse(self.live.fresh('inverter-600'))

    def test_samples_are_time_weighted(self):
        self.live.setup()
        self.live.handle(self.update('1000bbbbbb', power='10'))
        self.live.clock.t += 10
        self.live.handle(self.update('1000bbbbbb', power='40'))
        self.live.clock.t += 20
        self.live.tick()
        self.assertAlmostEqual(self.rows('samples', 'inverter-600')[-1]['power_w'], (10 * 10 + 40 * 20) / 30, places=2)

    def test_messages_for_other_devices_and_junk_are_ignored(self):
        self.live.setup()
        self.assertIsNone(self.live.handle(self.update('1000aaaaaa', power='999')))
        self.assertIsNone(self.live.handle('pong'))
        self.assertIsNone(self.live.handle('[1,2]'))
        self.assertIsNone(self.live.handle(self.update('1000bbbbbb', uiActive=60)))

    def test_reconnects_are_capped(self):
        self.live.connects = [self.live.wall() - 60] * V.MAX_CONNECTS
        self.assertFalse(self.live.connect_allowed())
        self.live.connects = [self.live.wall() - 90000] * V.MAX_CONNECTS
        self.assertTrue(self.live.connect_allowed())


class ConfigTests(unittest.TestCase):
    def test_live_switch_must_be_boolean(self):
        import run
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
            json.dump({'ewelink_live_enabled': 'no'}, f)
        with self.assertRaises(ValueError):
            run.load_config(f.name)
        Path(f.name).write_text(json.dumps({'ewelink_live_enabled': False}))
        self.assertFalse(run.load_config(f.name)['ewelink_live_enabled'])
        Path(f.name).unlink()


if __name__ == '__main__':
    unittest.main()
