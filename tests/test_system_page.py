"""Page checks for the System diagram: Epever dark-box wording, the mains charger box and the detail view. Python only; the Node part is skipped where Node is missing."""
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
        self.assertEqual(HTML.count('<g id="sysCharger" data-dev="charger"'), 2)
        self.assertEqual(sum(svg_section(k).count('>Charger<') for k in ('narrow', 'wide')), 2)
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
        for phrase in ("'Panel '+panel", "'not charging'", "'Float,'", "'battery full'", "'0.00 W in'"):
            self.assertIn(phrase, HTML)
        self.assertIn('Lit only while charging; a dark box says why', HTML)

    def test_epever_battery_min_today_shows_the_voltage_only(self):
        line = next(l for l in HTML.splitlines() if "set('BattMin'," in l)
        self.assertNotIn('as of', line)
        self.assertNotIn('toLocaleTimeString', line)
        self.assertIn("epeverText(v.batt_v_min_today,'V',2)", line)
        # Both views (Generation and the System detail) have the line.
        self.assertEqual(HTML.count('id="epeverBattMin"'), 1)
        self.assertEqual(HTML.count('id="sdEpBattMin"'), 1)

    @unittest.skipUnless(shutil.which('node'), 'Node is not installed (the Pi has none); the wording check runs on the Mac')
    def test_wording_and_charger_states_in_the_page_script(self):
        check = subprocess.run(['node', str(ROOT / 'tests' / 'check_system_page.cjs')], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)

    def test_charger_sits_below_the_epever_without_overlap(self):
        for kind, view_w in (('narrow', 390), ('wide', 640)):
            svg = svg_section(kind)
            self.assertIn('viewBox="0 0 %d ' % view_w, svg)
            found = boxes(svg)
            self.assertIn('sysCharger', found)
            self.assertIn('sysBatt', found)
            self.assertIn('sysEpever', found)
            cx, cy, cw, ch = found['sysCharger']
            bx, by, bw, bh = found['sysBatt']
            ex, ey, ew, eh = found['sysEpever']
            # Below the Epever, in the same column, with the same gap as the other rows.
            self.assertGreaterEqual(cy, ey + eh, kind)
            shed, garage = found['sysShed'], found['sysGarage']
            row_gap = garage[1] - (shed[1] + shed[3])
            self.assertGreaterEqual(cy - (ey + eh), row_gap - 1, kind)
            self.assertLess(abs(cx - ex), 1, kind)
            self.assertLessEqual(cx + cw, view_w)
            self.assertLessEqual(cy + ch, float(re.search(r'viewBox="0 0 %d ([\d.]+)"' % view_w, svg).group(1)))
            for gid, rect in found.items():
                if gid != 'sysCharger':
                    self.assertFalse(overlaps(found['sysCharger'], rect), '%s overlaps %s' % (kind, gid))
            # Charger line leaves the right side of the charger (mid-height),
            # runs up the right of the Epever, and enters the right side of the battery.
            path = re.search(r'id="sysL(?:W)?Charger"[^>]*d="M([\d.]+) ([\d.]+)H([\d.]+)V([\d.]+)H([\d.]+)"', svg)
            self.assertIsNotNone(path, kind)
            x0, y0, x_right, y_in, x1 = (float(n) for n in path.groups())
            self.assertAlmostEqual(x0, cx + cw, delta=2, msg=kind)
            self.assertGreater(y0, cy + ch * 0.25, kind)
            self.assertLess(y0, cy + ch * 0.75, kind)
            self.assertGreaterEqual(x_right, ex + ew + 12, kind)  # clear gap to the right of the Epever
            self.assertLess(x_right, view_w, kind)  # stays inside the viewBox
            self.assertAlmostEqual(x1, bx + bw, delta=2, msg=kind)  # enters the battery's right side
            self.assertGreater(y_in, by + bh * 0.25, kind)
            self.assertLess(y_in, by + bh * 0.75, kind)
            self.assertLess(y_in, by + bh - 8, kind)  # not the old bottom-right entry
            self.assertLess(x1, x_right, kind)  # last run is right-to-left, arrow points into the battery
            # Epever's own feed into the battery runs up the left-hand side.
            batt = re.search(r'id="sysL(?:W)?Batt"[^>]*d="M([\d.]+) ([\d.]+)V([\d.]+)"', svg)
            self.assertIsNotNone(batt, kind)
            lx, ly, ly2 = (float(n) for n in batt.groups())
            self.assertLess(lx, ex + ew / 2, kind)
            self.assertGreaterEqual(lx, ex, kind)
            self.assertAlmostEqual(ly, ey, delta=1, msg=kind)
            self.assertAlmostEqual(ly2, by + bh, delta=4, msg=kind)

    def test_inverter_plug_graphics_light_when_the_feed_is_on(self):
        # The three inverter plug drawings had no ids and no .on style, so they stayed grey.
        for name in ('sysPlugGardenUp', 'sysPlug1000', 'sysPlug600',
                     'sysPlugWGardenUp', 'sysPlugW1000', 'sysPlugW600'):
            self.assertEqual(HTML.count('id="%s"' % name), 1, name)
        self.assertIn('.sys-plan .sys-plug.on', HTML)
        self.assertIn("'Plug','PlugW'", HTML)
        # sysLine now toggles the plug with the same suffix as the feed line (1000, 600, GardenUp).
        self.assertIn("sysLine('1000',power.Inv1000>0)", HTML)
        self.assertIn("sysLine('600',power.Inv600>0)", HTML)
        self.assertIn("sysLine('GardenUp',power.GarInv>0)", HTML)


DEVICES = ['output', 'inv1000', 'inv600', 'garden', 'shed', 'garage', 'p275', 'epever', 'charger']
BOX_DEVICE = {'sysOutput': 'output', 'sysInv1000': 'inv1000', 'sysInv600': 'inv600',
              'sysGarInv': 'garden', 'sysPGarden': 'garden', 'sysGarden': 'garden',
              'sysPShed': 'shed', 'sysShed': 'shed', 'sysPGarage': 'garage', 'sysGarage': 'garage',
              'sysP275': 'p275', 'sys275': 'p275', 'sysEpever': 'epever', 'sysBatt': 'epever', 'sysCharger': 'charger'}


class SystemDetailPage(unittest.TestCase):
    def test_no_box_jumps_to_another_tab(self):
        self.assertNotIn('data-goto', HTML)
        for gone in ('sysGo', 'sysFocus', 'selectEwelinkDevice', 'role="link"'):
            self.assertNotIn(gone, HTML)

    def test_every_box_opens_one_of_the_nine_devices_in_both_layouts(self):
        for kind in ('narrow', 'wide'):
            found = dict(re.findall(r'<g id="(sys\w+)" data-dev="(\w+)"', svg_section(kind)))
            self.assertEqual(found, BOX_DEVICE, kind)
            for gid in BOX_DEVICE:
                tag = re.search(r'<g id="%s"[^>]*>' % gid, svg_section(kind)).group(0)
                for attr in ('tabindex="0"', 'role="button"', 'aria-expanded="false"', 'aria-controls="sysDetail"', 'aria-label="'):
                    self.assertIn(attr, tag, gid)
        self.assertEqual(set(BOX_DEVICE.values()), set(DEVICES))

    def test_nine_device_buttons_in_order(self):
        tabs = HTML.split('id="sysDetailTabs"', 1)[1].split('</div>', 1)[0]
        self.assertEqual(re.findall(r'<button type="button" role="tab" data-dev="(\w+)"', tabs), DEVICES)
        names = re.findall(r'</svg><span>([^<]+)</span><span class="tab-temp" data-watts>', tabs)
        self.assertEqual(names, ['Total to house', '1000 W inverter', '600 W inverter', 'Garden', 'Shed', 'Garage', '275',
                                 'Epever and battery', 'Charger'])
        script = HTML.split('const SYS_DEVICES=', 1)[1].split('};', 1)[0]
        self.assertEqual(re.findall(r'^\s*(\w+):\{name:', script, re.M), DEVICES)

    def test_detail_header_title_and_close_sit_above_a_full_width_period(self):
        self.assertIn('.sys-detail > .card-head { display:grid; grid-template-columns:minmax(0,1fr) auto;', HTML)
        self.assertIn('.sys-detail #sysDetailTitle { font-size:.9rem;', HTML)
        self.assertIn('border-top:1px solid var(--border)', HTML)
        header = HTML.split('id="sysDetail"', 1)[1].split('id="sdMeterView"', 1)[0]
        meter = HTML.split('id="sdMeterView"', 1)[1].split('id="sdOutView"', 1)[0]
        # Title and close share the first header row; Period is a full-width labelled select below the rule.
        self.assertIn('id="sysDetailTitle"', header)
        self.assertIn('id="sysDetailClose"', header)
        self.assertLess(header.find('id="sysDetailTitle"'), header.find('id="sysDetailClose"'))
        self.assertLess(header.find('id="sysDetailClose"'), header.find('id="sysDetailPeriod"'))
        self.assertIn('id="sysDetailPeriod" aria-label="Period"', header)
        self.assertNotIn('>Period <select', header)
        self.assertNotIn('<label for="sysDetailPeriod">', header)
        # Chart metric sits with the meter chart, not beside Period in the header.
        self.assertNotIn('id="sdMeterMetric"', header)
        self.assertIn('id="sdMeterMetric"', meter)
        self.assertIn('class="chart-metric"', meter)

    def test_detail_follows_the_home_graph_pattern(self):
        self.assertIn('@media(min-width:900px) {\n          .sys-body.detail-open { display:grid; grid-template-columns:minmax(0,7fr) minmax(0,5fr);', HTML)
        self.assertIn('.sys-body.detail-open .sys-narrow { display:block; }', HTML)
        self.assertIn('@media(max-width:899px) { .sys-body.detail-open .sys-plan-view { display:none; } }', HTML)
        self.assertIn('id="sysDetailClose"', HTML)
        self.assertIn("getBoundingClientRect().top<0)$('sysDetail').scrollIntoView", HTML)
        # One period picker that opens on Today; each device remembers its own choice.
        picker = HTML.split('id="sysDetailPeriod"', 1)[1].split('>', 1)[1].split('</select>', 1)[0]
        self.assertIn('<option value="today" selected>Today</option>', picker)
        self.assertIn("const sysPeriod=()=>sysPeriods[sysDev]||'today';", HTML)

    def test_history_only_for_the_open_device_and_aborted_on_close_and_tab_change(self):
        self.assertIn("const sysShowing=()=>activeTab==='system'&&!document.hidden&&!!sysDev;", HTML)
        load = HTML.split('async function loadSysDetail(){', 1)[1].split('\n}', 1)[0]
        self.assertTrue(load.strip().startswith('if(!sysShowing()||sysDetailController)return;'))
        stop = HTML.split('function stopSystem(){', 1)[1].split('\n', 1)[0]
        self.assertIn('sysDetailController?.abort()', stop)
        for name in ('function closeSysDetail(){', 'function selectSysDetail(dev){'):
            self.assertIn('sysDetailController?.abort()', HTML.split(name, 1)[1].split('\n', 2)[1])

    def test_shared_code_is_parameterised_not_copied(self):
        for once in ('function renderOutput(', 'function drawOutput(', 'function renderEpever(', 'function drawEpever(',
                     'function genChart(', 'function renderMeter(', "getJSON('/api/epever/history"):
            self.assertEqual(HTML.count(once), 1, once)
        self.assertEqual(HTML.count("'/api/tuya/history?device='"), 1)
        self.assertIn("const juwaiSolar=solarView('output','epever',", HTML)
        self.assertIn("const sysSolar=solarView('sdOut','sdEp',", HTML)
        ew = (ROOT / 'router' / 'web' / 'ewelink.js').read_text()
        for name in ('window.ewelinkGraph=graph', 'window.ewelinkDrawCard=drawCard', 'window.ewelinkEnergy='):
            self.assertIn(name, ew)
        self.assertEqual(ew.count("' (estimate)'"), 1)

    def test_total_wording_says_added_together_not_minus_epever(self):
        self.assertNotIn('600 watt inverter − Epever', HTML)
        self.assertEqual(HTML.count('1000 watt inverter + Garden inverter + 600 watt inverter, added together'), 2)

    def test_no_duplicate_ids_beyond_the_two_diagram_copies(self):
        ids = re.findall(r'\sid="([^"]+)"', HTML)
        repeated = {i for i in ids if ids.count(i) > 1}
        self.assertTrue(all(i.startswith('sys') for i in repeated), repeated)
        self.assertTrue(all(ids.count(i) == 2 for i in repeated), repeated)
        for i in repeated:
            self.assertEqual(svg_section('narrow').count('id="%s"' % i), 1, i)
            self.assertEqual(svg_section('wide').count('id="%s"' % i), 1, i)


if __name__ == '__main__':
    unittest.main()
