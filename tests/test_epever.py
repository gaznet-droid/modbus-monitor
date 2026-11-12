import contextlib
import json
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).parents[1] / 'router'))
import epever as E
import monitor

# Replies exactly as the dongle copied them on 12 Nov 2026 (readings only; no serial numbers).
PERIODIC = ['01020100a188', '0104080df500af187c000053fb', '01040c000000000000000003cd04d74692',
            '0104020064b8db', '0104060000000b00001151',
            '0104240bb40927000000000000000000000000000200000022000000c7000030e4000009690002c0c0',
            '01040200d978aa', '0104020b7e3e20', '0104040000000bba43', '0104020b7bfe23']
APP_OPEN = ['010101005188', '01020100a188', '0104040b7b00dbc9e2',
            '0104240bb40927000000000000000000000000000200000022000000c7000030e4000009690002c0c0',
            '0104060000000b00001151', '0104020064b8db', '01040c000000000000000003d304d5a755',
            '0104080e3000b91a630000ff5f']
MINUTE = ['0104020b80bfa0', '0104040000000bba43', '0104020b83ffa1']
RATED = '010410177007d0cb200000096007d0cb200000411d'


def decode(frames):
    reader, burst = E.Frames(), E.Burst()
    for words in reader.feed(bytes.fromhex(''.join(frames))):
        burst.add(words)
    return burst


class Decoding(unittest.TestCase):
    def test_periodic_burst(self):
        burst = decode(PERIODIC)
        self.assertEqual(burst.values, {
            'pv_v': 35.73, 'pv_a': 1.75, 'pv_w': 62.68, 'load_w': 0.0, 'batt_temp_c': 9.73,
            'device_temp_c': 12.39, 'load_on': 0, 'soc': 100, 'charge_state': 2,
            'gen_today_kwh': 0.34, 'gen_total_kwh': 1334.81, 'batt_a': 2.17, 'batt_v': 29.39})
        self.assertEqual(burst.status, {'battery': 0, 'charging': 11, 'discharging': 0})

    def test_burst_while_the_app_is_open_has_a_different_order(self):
        values = decode(APP_OPEN).values
        self.assertEqual((values['batt_v'], values['batt_a'], values['soc']), (29.39, 2.19, 100))
        self.assertEqual((values['pv_v'], values['pv_w'], values['gen_total_kwh']), (36.32, 67.55, 1334.81))

    def test_minute_poll_and_rated_data(self):
        self.assertEqual(decode(MINUTE).values, {'batt_v': 29.47, 'charge_state': 2})
        self.assertEqual(decode([RATED]).values, {})

    def test_small_single_word_is_only_soc_after_status_or_load(self):
        self.assertEqual(decode(['0104020064b8db']).values, {})

    def test_split_and_damaged_bytes(self):
        reader = E.Frames()
        data = bytes.fromhex('ff01' + PERIODIC[1] + '0104080df500af187c0000ffff' + PERIODIC[3])
        found = reader.feed(data[:9]) + reader.feed(data[9:20]) + reader.feed(data[20:])
        self.assertEqual(found, [[0x0df5, 0x00af, 0x187c, 0], [0x64]])


class StoreAndQueries(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / 'epever.sqlite3')
        self.now = [1_800_000_000.0]
        self.reader = E.CachedEpever(lambda: contextlib.nullcontext(0), lambda: None, clock=lambda: self.now[0],
                                     database_uri=lambda fd: 'file:' + self.path + '?mode=ro')

    def tearDown(self):
        self.tmp.cleanup()

    def fill(self):
        store = E.Store(self.path, lambda: None)
        store.metadata('name', 'Summer house')
        now = int(self.now[0] * 1000)
        early = E.Burst(); early.values.update(gen_total_kwh=1300.0, pv_w=10.0)
        store.add(now - 3 * 86400000, early)
        store.add(now - 240000, decode(PERIODIC))
        store.add(now - 60000, decode(MINUTE))
        store.close()

    def test_no_database_means_no_card(self):
        self.assertIsNone(self.reader.latest())
        self.assertIsNone(self.reader.history('today'))

    def test_latest_takes_each_reading_from_where_it_last_arrived(self):
        self.fill()
        latest = self.reader.latest()
        self.assertEqual(latest['name'], 'Summer house')
        self.assertEqual((latest['values']['pv_w'], latest['values']['batt_v']), (62.68, 29.47))
        self.assertEqual(latest['values']['charge_state_name'], 'Boost')
        self.assertEqual((latest['age_seconds'], latest['stale']), (240, False))
        self.now[0] += 3600
        self.reader.cache.clear()
        self.assertTrue(self.reader.latest()['stale'])

    def test_generation_is_the_counter_difference(self):
        self.fill()
        week = self.reader.history('7d')
        self.assertEqual(week['energy'], {'generated_kwh': 34.81, 'partial': True, 'estimated': False})
        self.assertEqual(week['columns'], ['ts', 'pv_w', 'batt_v', 'batt_a'])
        hour = self.reader.history('live')
        self.assertEqual(hour['energy'], {'generated_kwh': 34.81, 'partial': False, 'estimated': False})
        self.assertEqual([p[1:] for p in hour['points']], [[62.68, 29.39, 2.17], [None, 29.47, None]])
        with self.assertRaises(ValueError):
            self.reader.history('forever')

    def test_http(self):
        def serve(reader):
            class Live:
                def live(self, *a, **kw): return {}
            server = monitor.WebServer(('127.0.0.1', 0), monitor.handler(
                Live(), Path(monitor.__file__).parent / 'web', epever=reader))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            return server, 'http://127.0.0.1:%d' % server.server_port
        def code(url):
            try:
                with urllib.request.urlopen(url, timeout=3) as r: return r.status
            except urllib.error.HTTPError as error: return error.code
        server, base = serve(self.reader)
        try:
            self.assertEqual(code(base + '/api/epever/latest'), 404)
            self.fill(); self.reader.cache.clear()
            with urllib.request.urlopen(base + '/api/epever/history?window=today', timeout=3) as r:
                self.assertIn('energy', json.load(r))
            self.assertEqual(code(base + '/api/epever/latest'), 200)
            self.assertEqual(code(base + '/api/epever/history?window=bad'), 400)
            self.assertEqual(code(base + '/api/epever/latest?x=1'), 400)
        finally:
            server.shutdown(); server.server_close()
        server, base = serve(None)
        try:
            self.assertEqual(code(base + '/api/epever/latest'), 404)
        finally:
            server.shutdown(); server.server_close()


class Listening(unittest.TestCase):
    def test_stores_bursts_and_never_sends(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'epever.sqlite3')
            server = socket.socket(); server.bind(('127.0.0.1', 0)); server.listen(2)
            stop = threading.Event()
            def collect():  # the store belongs to the collector's own thread, as on the Pi
                store = E.Store(path, lambda: None)
                try: E.listen(server, store, stop, gap=0.3)
                finally: store.close()
            thread = threading.Thread(target=collect)
            thread.start()
            try:
                dongle = socket.create_connection(server.getsockname(), 2)
                dongle.sendall(bytes.fromhex(''.join(PERIODIC)))
                time.sleep(1.2)
                dongle.sendall(bytes.fromhex(''.join(MINUTE)))
                dongle.settimeout(1.2)
                with self.assertRaises(socket.timeout):
                    dongle.recv(1)  # the collector never writes to the dongle
                dongle.close()
            finally:
                stop.set(); thread.join(5); server.close()
            rows = sqlite3.connect(path).execute('SELECT pv_w,batt_v,charge_state,status FROM samples ORDER BY ts').fetchall()
            self.assertEqual(rows, [(62.68, 29.39, 2, '{"battery":0,"charging":11,"discharging":0}'),
                                    (None, 29.47, 2, '{"battery":0,"charging":11}')])

    def test_config(self):
        with tempfile.TemporaryDirectory() as folder:
            conf = Path(folder) / 'epever.json'
            conf.write_text('{"name":"Summer house"}')
            self.assertEqual(E.load_config(conf), {'port': 15001, 'name': 'Summer house', 'bind': '0.0.0.0'})
            conf.write_text('{"port":80}')
            with self.assertRaises(ValueError):
                E.load_config(conf)

    def test_module_has_no_way_to_send(self):
        source = Path(E.__file__).read_text()
        for word in ('.send(', '.sendall(', '.sendto(', 'create_connection'):
            self.assertNotIn(word, source)


if __name__ == '__main__':
    unittest.main()
