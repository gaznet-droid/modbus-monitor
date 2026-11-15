"""Page checks for the System diagram: Epever dark-box wording and the mains charger box."""
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
HTML = (ROOT / 'router' / 'web' / 'index.html').read_text()


def svg_section(kind):
    marker = 'class="sys-plan sys-%s"' % kind
    start = HTML.index(marker)
    end = HTML.index('class="sys-plan sys-wide"' if kind == 'narrow' else '<p class="sys-key"')
    return HTML[start:end]


def boxes(svg):
    found = {}
    for gid, x, y, w, h in re.findall(
            r'<g id="(sys\w+)"[^>]*>\s*<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"', svg):
        found[gid] = tuple(float(n) for n in (x, y, w, h))
    return found


def overlaps(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and ax + aw > bx and ay < by + bh and ay + ah > by


class SystemDiagramPage(unittest.TestCase):
    def test_charger_box_is_in_both_layouts_and_not_in_the_solar_total(self):
        self.assertEqual(HTML.count('id="sysCharger"'), 2)
        self.assertEqual(HTML.count('data-goto="ewelink:charger-inverter"'), 2)
        self.assertEqual(HTML.count('>Charger<'), 2)
        self.assertEqual(HTML.count('>mains, manual<'), 2)
        for name in ('sysLCharger', 'sysLWCharger', 'sysFCharger', 'sysFWCharger', 'sysChargerBig', 'sysChargerS1', 'sysChargerS2'):
            self.assertEqual(HTML.count('id="%s"' % name), 1 if name.startswith('sysL') or name.startswith('sysF') else 2)
        self.assertIn("OUTPUT_IDS=['inverter-1000','garage-inverter','inverter-600']", HTML)
        self.assertNotIn('charger-inverter', HTML.split('OUTPUT_IDS=')[1].split(']', 1)[0])
        system = HTML.split('function renderSystem', 1)[1].split('async function readSystem', 1)[0]
        parts = system.split('const parts=', 1)[1].split(']', 1)[0]
        self.assertEqual(parts, '[power.Inv1000,power.GarInv,power.Inv600')
        self.assertNotIn('Charger', parts)
        self.assertNotIn('charger-inverter', parts)
        self.assertIn('mains charger is not added', HTML)
        self.assertIn('not part of the solar total', HTML)
        self.assertIn('separate mains input', HTML)
        # The Epever dark-box phrases are in the page script, split so they fit the narrow box.
        for phrase in ("'Panel '+panel", "'not charging'", "'Float,'", "'battery full'", "'0 W in'"):
            self.assertIn(phrase, HTML)
        self.assertIn('Lit only while charging; a dark box says why', HTML)

    @unittest.skipUnless(shutil.which('node'), 'Node is not installed (the Pi has none); the wording check runs on the Mac')
    def test_wording_and_charger_states_in_the_page_script(self):
        check = subprocess.run(['node', str(ROOT / 'tests' / 'check_system_page.cjs')], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)

    def test_charger_sits_to_the_right_of_the_battery_without_overlap(self):
        for kind, view_w in (('narrow', 464), ('wide', 744)):
            svg = svg_section(kind)
            self.assertIn('viewBox="0 0 %d ' % view_w, svg)
            found = boxes(svg)
            self.assertIn('sysCharger', found)
            self.assertIn('sysBatt', found)
            cx, cy, cw, ch = found['sysCharger']
            bx, by, bw, bh = found['sysBatt']
            self.assertGreaterEqual(cx, bx + bw, kind)
            self.assertLessEqual(cx + cw, view_w)
            self.assertLessEqual(cy + ch, float(re.search(r'viewBox="0 0 %d ([\d.]+)"' % view_w, svg).group(1)))
            for gid, rect in found.items():
                if gid != 'sysCharger':
                    self.assertFalse(overlaps(found['sysCharger'], rect), '%s overlaps %s' % (kind, gid))
            # The flow line runs from the charger's left edge leftwards into the battery.
            path = re.search(r'id="sysL(?:W)?Charger"[^>]*d="M([\d.]+) ([\d.]+)H([\d.]+)"', svg)
            self.assertIsNotNone(path, kind)
            start, y, end = (float(n) for n in path.groups())
            self.assertEqual(start, cx)
            self.assertLess(end, start)
            self.assertGreater(end, bx + bw)
            self.assertLess(end, cx)
            self.assertGreater(y, cy)
            self.assertLess(y, cy + ch)


if __name__ == '__main__':
    unittest.main()
