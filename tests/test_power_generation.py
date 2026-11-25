"""Page checks for the generation chart on the Power tab (step 1 of retiring the Generation tab). Python only."""
import re
import unittest
from pathlib import Path

HTML = (Path(__file__).parents[1] / 'router' / 'web' / 'index.html').read_text()


class PowerGenerationCard(unittest.TestCase):
    def test_card_sits_between_the_net_power_chart_and_the_energy_flow(self):
        panel = HTML.split('id="powerPanel"', 1)[1].split('</section>', 1)[0]
        self.assertLess(panel.index('id="lineChart"'), panel.index('id="genCard"'))
        self.assertLess(panel.index('id="genCard"'), panel.index('id="flowCard"'))
        for name in ('genChart', 'genMetric', 'genMobileChart', 'genMeterButtons', 'genBucketChoice', 'genLimit'):
            self.assertEqual(HTML.count('id="%s"' % name), 1, name)

    def test_every_element_the_power_chart_names_exists_once(self):
        call = HTML.split('const genView=genChart(', 1)[1].split('}', 1)[0]
        for name in re.findall(r":'(\w+)'", call):
            self.assertEqual(HTML.count('id="%s"' % name), 1, name)

    def test_periods_map_to_history_windows_and_cap_at_30_days(self):
        windows = HTML.split('const GEN_WINDOWS=', 1)[1].split('};', 1)[0]
        allowed = {'15m', 'live', '2h', '4h', '8h', 'today', '24h', '7d', '30d'}
        mapped = dict(re.findall(r"'(\w+)':'(\w+)'", windows))
        self.assertEqual(set(mapped.values()) - allowed, set())
        for period in ('90d', '365d', 'custom'):
            self.assertNotIn(period, mapped)
        self.assertIn("goes back 30 days at most, so it shows the past 30 days.", HTML)

    def test_mobile_summary_keeps_period_only_under_the_title(self):
        self.assertNotIn('id="genMobilePeriod"', HTML)
        card = HTML.split('id="genCard"', 1)[1].split('id="flowCard"', 1)[0]
        self.assertLess(card.index('id="genPeriod"'), card.index('id="genMobileTotal"'))
        self.assertLess(card.index('id="genMobileSplit"'), card.index('mobile-generation-label'))
        self.assertIn('>Estimated</span>', card)
        self.assertNotIn('estimated generation', card)

    def test_mobile_compare_includes_the_total_series(self):
        # Compare used to copy only Shed and Garage (indexes 0 and 1). Total is the
        # dataset appended after the meter series; it must be on the Compare chart too.
        self.assertIn("v.meter==='compare'?[...shown.map(at),...(power?[totalI]:[])]", HTML)
        self.assertIn("...(power?['Total']:[])", HTML)

    def test_power_tab_generation_includes_275_and_garden_together(self):
        # Dad, 25 Nov: the 275 watt panels and the 200 watt Garden (read from the Garden inverter's plug)
        # are one source on the Power tab's Generation card, with one button, and count in Total.
        buttons = HTML.split('id="genMeterButtons"', 1)[1].split('</div>', 1)[0]
        self.assertIn('data-meter="combo"', buttons)
        self.assertNotIn('data-meter="garden"', buttons)
        self.assertIn("/api/ewelink/history?device=garage-inverter&window='+p.window", HTML)
        self.assertIn("history.combo=comboMeter(history.p275,g)", HTML)
        self.assertIn("()=>activeTab==='power',['shed','garage','combo'])", HTML)
        self.assertIn("const parts=()=>['shed','garage',...(v.ids.includes('combo')?['combo']:[])]", HTML)

    def test_reads_only_while_the_power_tab_is_showing(self):
        body = HTML.split('async function readGen(){', 1)[1].split('\n}', 1)[0]
        self.assertTrue(body.strip().startswith("if(activeTab!=='power'||document.hidden)return;"))
        self.assertIn("activeTab!=='power'", body.split('catch', 1)[1])
        self.assertIn('genController?.abort()', HTML.split('function selectTab', 1)[1].split('\n}', 1)[0])


if __name__ == '__main__':
    unittest.main()
