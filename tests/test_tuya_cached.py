import contextlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from urllib.request import urlopen, Request
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).parents[1] / 'router'))
import tuya_collector as collector
from tuya_cached import CachedTuya
import monitor


class CachedTuyaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = int(time.time() * 1000)
        self.elapsed = 0
        self.available = True
        self.opens = 0
        self.writer = collector.USBStore(self.root / 'tuya.sqlite3', self.guard)
        self.writer.add('shed', self.now, dict(voltage_v=26.47, current_a=.038, power_w=1.02),
                        {'private_raw_field': 'not public'})
        self.cached = CachedTuya(self.directory, self.guard, clock=lambda: self.now / 1000,
                                 monotonic=lambda: self.elapsed,
                                 database_uri=lambda _: (self.root / 'tuya.sqlite3').as_uri() + '?mode=ro')

    def tearDown(self):
        self.writer.close()
        self.temp.cleanup()

    def guard(self):
        if not self.available:
            raise RuntimeError('USB absent')

    @contextlib.contextmanager
    def directory(self):
        self.opens += 1
        self.guard()
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            yield fd
        finally:
            os.close(fd)

    def test_multiple_clients_share_cache_and_age_updates_without_fabricated_values(self):
        first = self.cached.latest()
        self.assertEqual(first['collection_mode'], 'manual')
        self.assertIsNone(first['devices'][1]['values']['power_w'])
        self.assertTrue(first['devices'][1]['stale'])
        self.assertNotIn('private_raw_field', json.dumps(first))
        first['devices'][0]['values']['power_w'] = 999
        self.assertEqual(self.cached.latest()['devices'][0]['values']['power_w'], 1.02)
        self.now += 1801000
        self.assertTrue(self.cached.latest()['devices'][0]['stale'])
        self.assertEqual(self.opens, 1)

    def test_usb_loss_is_negative_cached_and_never_creates_a_fallback(self):
        self.cached.latest()
        self.available = False
        self.elapsed = 31
        for _ in range(3):
            with self.assertRaises(RuntimeError):
                self.cached.latest()
        self.assertEqual(self.opens, 2)
        self.available = True
        self.elapsed = 37
        self.assertTrue(self.cached.latest()['storage_available'])
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['tuya.sqlite3'])

    def test_indexed_bounded_history_and_no_network_on_any_read(self):
        with self.writer.db:
            self.writer.db.executemany('INSERT INTO rollups VALUES (?,?,?,?,?,?,?,?,?,?)',
                [('shed', 60, self.now - 604800000 + i * 60000, 1, 26., 1, .1, 1, 2., 1)
                 for i in range(10079)])
        plan = self.writer.db.execute('EXPLAIN QUERY PLAN SELECT ts FROM samples '
                                     'WHERE device=? ORDER BY ts DESC LIMIT 1', ('shed',)).fetchall()
        self.assertTrue(any('INDEX' in row[3] for row in plan))
        result = self.cached.history('shed', '7d')
        self.assertLessEqual(len(result['points']), 1000)
        self.assertGreater(len(result['points']), 500)
        self.assertEqual(self.cached.history('shed', '7d'), result)
        self.assertEqual(self.opens, 1)
        for device, window in [('solar', '24h'), ('shed', 'all'), ('../shed', '24h')]:
            with self.assertRaises(ValueError):
                self.cached.history(device, window)

    def test_http_contract_rejects_writes_and_bounds_queries(self):
        store = monitor.Store(self.root / 'monitor.sqlite3')
        store.add(self.now, 100)
        server = monitor.WebServer(('127.0.0.1', 0), monitor.handler(
            store, Path(__file__).parents[1] / 'router/web', tuya=self.cached))
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        base = 'http://127.0.0.1:' + str(server.server_port)
        try:
            with urlopen(base + '/api/tuya/latest') as response:
                self.assertEqual(len(json.load(response)['devices']), 2)
            for path, code in [('/api/tuya/latest?refresh=1', 400),
                ('/api/tuya/history?device=solar&window=24h', 400),
                ('/api/tuya/history?device=shed&device=garage&window=24h', 400),
                ('/api/tuya/history?device=shed&window=24h&start=0', 400),
                ('/tuya.sqlite3', 404), ('/etc/modbus-tuya.json', 404)]:
                with self.assertRaises(HTTPError) as error:
                    urlopen(base + path)
                self.assertEqual(error.exception.code, code)
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(base + '/api/tuya/latest', data=b'{}', method='POST'))
            self.assertEqual(error.exception.code, 501)
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
            store.db.close()


class GenerationTests(unittest.TestCase):
    def test_constant_power_clipped_period_and_gap_exclusion(self):
        from tuya_cached import estimate_energy
        rows=[(i*900000,1000) for i in range(9)]
        self.assertAlmostEqual(estimate_energy(rows,0,7200000)['estimated_kwh'],2)
        self.assertAlmostEqual(estimate_energy(rows,450000,6750000)['estimated_kwh'],1.75)
        self.assertIsNone(estimate_energy([(0,1000),(3600000,1000)],0,3600000)['estimated_kwh'])
        self.assertIsNone(estimate_energy([(0,1000)],0,3600000)['estimated_kwh'])
        self.assertAlmostEqual(estimate_energy([(0,0),(900000,2000)],0,900000)['estimated_kwh'],.25)

    def test_invalid_samples_and_no_extrapolation(self):
        from tuya_cached import estimate_energy
        self.assertIsNone(estimate_energy([(0,1000),(900000,None),(1800000,1000)],0,1800000)['estimated_kwh'])
        e=estimate_energy([(900000,1000),(1800000,1000)],0,3600000)
        self.assertAlmostEqual(e['estimated_kwh'],.25)
        self.assertEqual(e['coverage_s'],900)

    def test_uk_calendar_days_handle_clock_changes(self):
        from tuya_cached import period_bounds
        from datetime import datetime,timezone
        now=int(datetime(2026,10,26,12,tzinfo=timezone.utc).timestamp()*1000)
        start,end=period_bounds('yesterday',now)
        self.assertEqual(end-start,25*3600000)
        now=int(datetime(2026,3,30,12,tzinfo=timezone.utc).timestamp()*1000)
        start,end=period_bounds('yesterday',now)
        self.assertEqual(end-start,23*3600000)
        self.assertEqual(period_bounds('previous24h',now),(now-2*86400000,now-86400000))

    def test_history_energy_uses_retained_rollups_after_raw_samples_expire(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'tuya.sqlite3'
            store=collector.USBStore(path,lambda:None)
            now=1793000000000//60000*60000
            for i in range(5):
                store.add('shed',now-3600000+i*900000,
                    dict(voltage_v=24,current_a=1,power_w=1000),{})
            with store.db: store.db.execute('DELETE FROM samples')
            @contextlib.contextmanager
            def directory(): yield 0
            cached=CachedTuya(directory,lambda:None,clock=lambda:now/1000,
                database_uri=lambda _:path.as_uri()+'?mode=ro')
            result=cached.history('shed','live')
            self.assertAlmostEqual(result['energy']['estimated_kwh'],1)
            self.assertEqual(result['energy']['coverage_s'],3600)
            self.assertIsNone(cached.history('garage','live')['energy']['estimated_kwh'])
            store.close()


class CloudSafeguardTests(unittest.TestCase):
    def test_budgets_and_cycle_gate_survive_process_restarts(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'tuya.sqlite3'
            now = [1792750000]
            store = collector.USBStore(path, lambda: None)
            budget = collector.RequestBudget(store, 2, 3, clock=lambda: now[0])
            budget.reserve()
            self.assertEqual(budget.claim_cycle(900), 0)
            store.close()
            store = collector.USBStore(path, lambda: None)
            budget = collector.RequestBudget(store, 2, 3, clock=lambda: now[0])
            self.assertEqual(budget.claim_cycle(900), 900)
            budget.reserve()
            with self.assertRaises(collector.PollingStopped):
                budget.reserve()
            now[0] += 86400
            budget.reserve()
            with self.assertRaises(collector.PollingStopped):
                budget.reserve()
            now[0] -= 86400
            with self.assertRaises(collector.PollingStopped):
                budget.reserve()
            store.close()

    def test_deadline_blocks_transport_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'tuya.sqlite3'
            for _ in range(2):
                store = collector.USBStore(path, lambda: None)
                budget = collector.RequestBudget(store, 300, 10000,
                    clock=lambda: 200, expires_at=200)
                cloud = collector.TuyaCloud('id', 'secret',
                    transport=lambda *_args, **_kwargs: self.fail('Network after expiry'),
                    before_request=budget.reserve)
                with self.assertRaises(collector.PollingStopped):
                    cloud.token()
                self.assertIsNone(store.metadata('request_budget'))
                store.close()

    def test_approved_schedule_requires_future_deadline_without_network(self):
        with tempfile.TemporaryDirectory() as folder:
            store = collector.USBStore(Path(folder) / 'tuya.sqlite3', lambda: None)
            cloud = collector.TuyaCloud('id', 'secret',
                transport=lambda *_args, **_kwargs: self.fail('Network without deadline'))
            conf = dict(approved_cloud_polling=True, daily_request_budget=300,
                        monthly_request_budget=10000)
            for deadline in (None, True, float('nan'), time.time() - 1):
                conf['cloud_stop_epoch'] = deadline
                with self.assertRaises(collector.PollingStopped):
                    collector.run_cloud(cloud, conf, store, threading.Event())
                self.assertIsNone(store.metadata('collection_mode'))
            store.close()

    def test_process_lock_prevents_competing_collectors(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'collector.lock')
            with collector.process_lock(path):
                with self.assertRaises(RuntimeError):
                    with collector.process_lock(path):
                        self.fail('Competing lock acquired')
            with collector.process_lock(path):
                pass

    def test_unapproved_cloud_schedule_does_no_network_or_state_change(self):
        with tempfile.TemporaryDirectory() as folder:
            store = collector.USBStore(Path(folder) / 'tuya.sqlite3', lambda: None)
            cloud = collector.TuyaCloud('id', 'secret', transport=lambda *_: self.fail('Network call'))
            with self.assertRaises(RuntimeError):
                collector.run_cloud(cloud, {}, store, threading.Event())
            self.assertIsNone(store.metadata('collection_mode'))
            store.close()

    def test_auth_failure_stops_other_devices_and_refresh_is_bounded(self):
        calls = []
        class Cloud:
            token_failure = True
            def status(self, ident):
                calls.append(ident)
                raise collector.TuyaError('HTTP-401')
        errors = {}
        self.assertEqual(collector.collect_once(Cloud(), {'shed': 'a', 'garage': 'b'}, errors=errors), 0)
        self.assertEqual(calls, ['a'])
        self.assertEqual(errors, {'shed': 'HTTP-401'})

    def test_storage_failure_does_not_consume_request_budget_or_allow_network(self):
        with tempfile.TemporaryDirectory() as folder:
            ready = [True]
            def guard():
                if not ready[0]: raise RuntimeError('USB missing')
            store = collector.USBStore(Path(folder) / 'tuya.sqlite3', guard)
            budget = collector.RequestBudget(store, 10, 100)
            ready[0] = False
            cloud = collector.TuyaCloud('id', 'secret', transport=lambda *_: self.fail('Network call'),
                                        before_request=budget.reserve)
            with self.assertRaises(RuntimeError): cloud.token()
            store.close()


if __name__ == '__main__':
    unittest.main()
