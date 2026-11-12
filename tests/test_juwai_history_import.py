import contextlib
import io
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'router'))
import juwai_history_import as H
import juwai_local as J
import tuya_collector as collector
from tuya_cached import estimate_energy

MIN, HOUR, DAY = 60000, 3600000, 86400000
T0 = 1_700_000_000_000 // DAY * DAY  # an arbitrary midnight UTC
JOIN = T0 + 8 * DAY + 5 * MIN  # where the shed's local history starts (minute aligned)
POWER = {'shed': 100.0, 'garage': 40.0}


def out(power):
    return {'voltage_v': 24.0, 'current_a': power / 24.0, 'power_w': power}


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.cloud_path, self.local_path = self.root / 'tuya.sqlite3', self.root / 'juwai_local.sqlite3'
        self.lock = self.root / 'juwai-local.lock'
        cloud = collector.USBStore(self.cloud_path, lambda: None)
        cloud.db.execute('PRAGMA synchronous=OFF')  # tests only: fast fixtures
        cloud.last_cleanup = float('inf')  # build everything first, then prune as the real cloud collector does
        # Cloud: a sample every 15 minutes for eight days, carrying on past the join (it keeps running).
        self.cloud_rows = {}
        for device, power in POWER.items():
            for ts in range(T0, JOIN + DAY, 15 * MIN):
                cloud.add(device, ts, out(power), {'20': 2400})
                self.cloud_rows.setdefault(device, []).append(ts)
        with cloud.db:  # raw samples older than a week are gone; their rollups remain
            cloud.db.execute('DELETE FROM samples WHERE ts<?', (JOIN + DAY - 7 * DAY,))
        cloud.close()
        local = collector.USBStore(self.local_path, lambda: None)
        local.db.executescript(J.EXTRA_SCHEMA)
        local.db.execute('PRAGMA synchronous=OFF')
        # Local: a sample a minute for six hours; the garage starts a few minutes after the shed.
        self.local_ts = {'shed': range(JOIN, JOIN + 6 * HOUR, MIN),
                         'garage': range(JOIN + 7 * MIN, JOIN + 7 * MIN + 6 * HOUR, MIN)}
        for device, times in self.local_ts.items():
            for ts in times:
                local.add(device, ts, out(POWER[device]), {'20': 2400})
        local.close()
        self.cloud_before = self.dump(self.cloud_path)

    def tearDown(self):
        self.tmp.cleanup()

    def dump(self, path):
        db = sqlite3.connect(path)
        try:
            return {t: db.execute('SELECT * FROM %s ORDER BY 1,2%s' % (t, '' if t == 'metadata' else ',3')).fetchall()
                    for t in ('samples', 'rollups', 'metadata')}
        finally:
            db.close()

    def run_tool(self, *flags, paths=True):
        argv = list(flags)
        if paths:
            argv += ['--cloud-db', str(self.cloud_path), '--local-db', str(self.local_path),
                     '--lock-file', str(self.lock)]
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            try:
                code = H.main(argv)
            except SystemExit as exc:
                code = exc.code
        return code, buf.getvalue(), err.getvalue()

    def test_refuses_without_explicit_paths_a_mode_or_an_existing_local_database(self):
        self.assertEqual(self.run_tool('--apply', paths=False)[0], 2)
        self.assertEqual(self.run_tool()[0], 2)  # paths but neither --dry-run nor --apply
        before = self.dump(self.local_path)
        missing = self.root / 'missing.sqlite3'
        self.assertEqual(self.run_tool('--dry-run', '--cloud-db', str(self.cloud_path),
                                       '--local-db', str(missing), '--lock-file', str(self.lock),
                                       paths=False)[0], 2)
        self.assertFalse(missing.exists())
        self.assertEqual(self.dump(self.local_path), before)

    def test_dry_run_reports_counts_and_range_and_changes_nothing(self):
        before = self.dump(self.local_path)
        code, text, _ = self.run_tool('--dry-run')
        self.assertEqual(code, 0)
        minutes = sum(1 for ts in self.cloud_rows['shed'] if ts < JOIN // MIN * MIN)  # one sample per minute bucket
        raw = sum(1 for ts in self.cloud_rows['shed'] if ts < JOIN and ts >= JOIN + DAY - 7 * DAY)
        self.assertIn('would copy shed: %d minute rollups' % minutes, text)
        self.assertIn('and %d raw samples' % raw, text)
        self.assertIn('would copy garage:', text)
        self.assertIn('UTC', text)
        self.assertEqual(self.dump(self.local_path), before)
        self.assertFalse(self.lock.exists())  # a dry run does not even take the lock

    def test_apply_copies_only_older_samples_and_rolls_up_like_the_collector(self):
        self.assertEqual(self.run_tool('--apply')[0], 0)
        local = self.dump(self.local_path)
        ref = collector.USBStore(self.root / 'ref.sqlite3', lambda: None)
        ref.db.execute('PRAGMA synchronous=OFF')
        ref.last_cleanup = float('inf')  # the reference is the unpruned truth
        for device, times in self.local_ts.items():
            cutoff = times[0] // MIN * MIN
            got = [r[1] for r in local['samples'] if r[0] == device and r[1] not in times]
            old = [ts for ts in self.cloud_rows[device] if ts < cutoff]
            self.assertEqual(got, [ts for ts in old if ts >= JOIN + DAY - 7 * DAY])  # raw the cloud still had
            self.assertTrue(old and old[-1] < cutoff <= times[0])  # none at or after the join
            for ts in [*old, *times]:  # the same samples fed through the collector's own add
                ref.add(device, ts, out(POWER[device]), {})
        want = ref.db.execute('SELECT * FROM rollups ORDER BY 1,2,3').fetchall()
        ref.close()
        self.assertEqual(local['rollups'], want)
        self.assertEqual(self.dump(self.cloud_path), self.cloud_before)  # source untouched

    def test_second_run_changes_nothing_and_does_not_double_count(self):
        self.run_tool('--apply')
        first = self.dump(self.local_path)
        code, text, _ = self.run_tool('--apply')
        self.assertEqual(code, 0)
        self.assertIn('already imported', text)
        self.assertEqual(self.dump(self.local_path), first)

    def test_rerun_after_raw_samples_were_pruned_does_not_double_count(self):
        self.run_tool('--apply')
        db = sqlite3.connect(self.local_path)
        db.execute('DELETE FROM samples WHERE ts<?', (JOIN,)); db.commit()  # the collector's 7-day pruning
        rollups = db.execute('SELECT * FROM rollups ORDER BY 1,2,3').fetchall(); db.close()
        self.run_tool('--apply')
        db = sqlite3.connect(self.local_path)
        self.assertEqual(db.execute('SELECT * FROM rollups ORDER BY 1,2,3').fetchall(), rollups)
        self.assertEqual(db.execute('SELECT COUNT(*) FROM samples WHERE ts<?', (JOIN,)).fetchone()[0], 0)
        db.close()

    def test_refuses_while_the_collector_holds_its_lock(self):
        before = self.dump(self.local_path)
        with collector.process_lock(self.lock):
            code, _, err = self.run_tool('--apply')
        self.assertEqual(code, 2); self.assertIn('Stop the local collector', err)
        self.assertEqual(self.dump(self.local_path), before)

    def test_meter_with_no_local_history_is_skipped(self):
        db = sqlite3.connect(self.local_path)
        db.execute("DELETE FROM samples WHERE device='garage'"); db.execute("DELETE FROM rollups WHERE device='garage'")
        db.commit(); db.close()
        code, text, _ = self.run_tool('--apply')
        self.assertEqual(code, 0); self.assertIn('garage: no local history yet', text)
        db = sqlite3.connect(self.local_path)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM samples WHERE device='garage'").fetchone()[0], 0)
        db.close()

    def test_energy_estimates_across_the_join_look_sane(self):
        # A steady 100 W on both sides of the join. Cloud samples are 15 minutes apart (inside the
        # 30 minute gap limit), local ones a minute apart; neither side may lose or double energy.
        self.run_tool('--apply')

        @contextlib.contextmanager
        def directory():
            fd = os.open(self.root, os.O_RDONLY)
            try: yield fd
            finally: os.close(fd)
        now = JOIN + 3 * HOUR
        cached = J.CachedLocal(directory, lambda: None, clock=lambda: now / 1000,
                               database_uri=lambda fd: 'file:' + str(self.local_path) + '?mode=ro')
        history = cached.history('shed', '7d')
        energy = history['energy']
        self.assertLess(history['start'], JOIN - 6 * DAY)  # the window really spans the join
        hours = energy['coverage_s'] / 3600
        self.assertAlmostEqual(hours, (history['end'] - history['start']) / HOUR, delta=0.5)  # no gaps
        self.assertAlmostEqual(energy['estimated_kwh'], 0.1 * hours, delta=0.05)
        self.assertTrue(all(abs(p[3] - 100 / 24.0) < 1e-6 for p in history['points']))
        # The same sum done directly over a window straddling the join.
        db = sqlite3.connect(self.local_path)
        a, b = JOIN - 5 * HOUR, JOIN + HOUR
        rows = db.execute("SELECT bucket, power_sum/NULLIF(power_n,0) FROM rollups WHERE device='shed' "
                          "AND period=60 AND bucket>=? AND bucket<=? ORDER BY bucket", (a - 1800000, b + 1800000))
        direct = estimate_energy(rows, a, b)
        db.close()
        self.assertAlmostEqual(direct['estimated_kwh'], 0.6, delta=0.01)
        self.assertAlmostEqual(direct['coverage_s'], 6 * 3600, delta=60)


if __name__ == '__main__':
    unittest.main()
