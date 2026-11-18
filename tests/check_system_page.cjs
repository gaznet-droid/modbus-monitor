// Run the System-tab helpers from index.html against sample readings. No browser, no network.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync('router/web/index.html', 'utf8');
const start = html.indexOf('function sysNum');
const end = html.indexOf('async function readSystem');
assert.ok(start > 0 && end > start, 'system helpers not found');
const byId = new Map();
function cls() {
  const set = new Set();
  return {
    toggle(name, on) { if (on) set.add(name); else set.delete(name); },
    contains(name) { return set.has(name); },
  };
}
function make(id) {
  return { id, textContent: '', dataset: {}, classList: cls(), setAttribute() {} };
}
function pair(id) {
  if (!byId.has(id)) byId.set(id, [make(id), make(id)]);
  return byId.get(id);
}
const status = { textContent: '' };
const context = {
  console, Number, Math, Object, Array, String, isNaN, parseInt,
  document: { querySelectorAll: sel => pair(sel.slice(1)) },
  $: id => (id === 'systemStatus' ? status : { textContent: '' }),
  homeTime: t => 'at ' + t,
};
vm.createContext(context);
vm.runInContext(html.slice(start, end), context);
const text = id => pair(id)[0].textContent;
const state = id => pair(id)[0].dataset.state;
const both = id => {
  const [a, b] = pair(id);
  assert.equal(a.textContent, b.textContent, id + ' text differs between layouts');
  assert.equal(a.dataset.state, b.dataset.state, id + ' state differs between layouts');
};
function draw(epever, devices) {
  context.renderSystem([{ devices: [] }, epever, { devices }]);
}
function epever(values, extra) {
  draw(Object.assign({ stale: false, observed_at: 1, values }, extra), []);
}
const plug = (over) => Object.assign({ id: 'charger-inverter', ts: 5, stale: false, online: true, power_w: 0, voltage_v: 230, current_a: 0.5, switch: 'on' }, over);
const inverters = (w) => ['inverter-1000', 'garage-inverter', 'inverter-600'].map((id, i) => ({ id, ts: 5, stale: false, online: true, power_w: w[i], switch: 'on' }));

epever({ pv_w: 0, pv_v: 32.6, charge_state: 0, charge_state_name: 'Not charging', gen_today_kwh: 1.2, batt_v: 25 });
assert.equal(state('sysEpever'), 'idle');
assert.equal(text('sysEpeverBig'), '0.00 W in');
assert.equal(text('sysEpeverS1'), 'Panel 32.60 V');
assert.equal(text('sysEpeverS2'), 'not charging');
assert.equal(text('sysEpeverS3'), '1,200 Wh today');
both('sysEpeverS1');

epever({ pv_w: 12, pv_v: 32.6, charge_state: 0, charge_state_name: 'Not charging' });
assert.equal(state('sysEpever'), 'idle');
assert.equal(text('sysEpeverBig'), '12.00 W in');
assert.equal(text('sysEpeverS1'), 'Panel 32.60 V');
assert.equal(text('sysEpeverS2'), 'not charging');

epever({ pv_w: 0, charge_state: 0, charge_state_name: 'Not charging' });
assert.equal(text('sysEpeverS1'), 'Not charging');
assert.equal(text('sysEpeverS2'), '0.00 W in');

epever({ pv_w: 4, charge_state: 0, charge_state_name: 'Not charging' });
assert.equal(state('sysEpever'), 'idle');
assert.equal(text('sysEpeverBig'), '4.00 W in');
assert.equal(text('sysEpeverS1'), 'not charging');
assert.equal(text('sysEpeverS2'), '');

epever({ pv_w: 0, pv_v: 18, charge_state: 1, charge_state_name: 'Float' });
assert.notEqual(state('sysEpever'), 'lit');
assert.equal(text('sysEpeverS1'), 'Float,');
assert.equal(text('sysEpeverS2'), 'battery full');

epever({ pv_w: 0.4, pv_v: 30, charge_state: 1, charge_state_name: 'Float' });
assert.equal(state('sysEpever'), 'lit');
assert.equal(text('sysEpeverBig'), '0.40 W in');
assert.equal(text('sysEpeverS1'), 'Float,');
assert.equal(text('sysEpeverS2'), 'battery full');

epever({ pv_w: 40, pv_v: 32.6, charge_state: 1, charge_state_name: 'Float', gen_today_kwh: 2 });
assert.equal(state('sysEpever'), 'lit');
assert.equal(text('sysEpeverBig'), '40.00 W in');
assert.equal(text('sysEpeverS1'), 'Float');
assert.equal(text('sysEpeverS2'), '32.60 V panel');
assert.equal(text('sysEpeverS3'), '2,000 Wh today');

epever({ pv_w: 0, charge_state: 2, charge_state_name: 'Boost', pv_v: 10 });
assert.notEqual(state('sysEpever'), 'lit');
assert.equal(text('sysEpeverBig'), '0.00 W in');
assert.equal(text('sysEpeverS1'), 'Boost');
assert.equal(text('sysEpeverS2'), '0.00 W in');

epever({ pv_w: 0, charge_state: 3, charge_state_name: 'Equalising' });
assert.equal(text('sysEpeverS1'), 'Equalising');
assert.equal(text('sysEpeverS2'), '0.00 W in');
assert.notEqual(state('sysEpever'), 'lit');

epever({ pv_w: 55, pv_v: 60, charge_state: 2, charge_state_name: 'Boost' });
assert.equal(state('sysEpever'), 'lit');
assert.equal(text('sysEpeverS1'), 'Boost');
assert.equal(text('sysEpeverS2'), '60.00 V panel');

draw({ stale: true, observed_at: 1, values: { pv_w: 10, pv_v: 32.6, charge_state: 2, charge_state_name: 'Boost' } }, []);
assert.equal(state('sysEpever'), 'stale');
assert.equal(text('sysEpeverBig'), '—');
assert.equal(text('sysEpeverS1'), 'Old reading');
assert.equal(text('sysEpeverS2'), '');
assert.equal(text('sysEpeverS3'), '');

draw(null, []);
assert.equal(state('sysEpever'), 'stale');
assert.equal(text('sysEpeverBig'), '—');
assert.equal(text('sysEpeverS1'), 'No reading');

draw({ stale: false, observed_at: 1, values: { pv_v: 32.6, charge_state: 0, charge_state_name: 'Not charging' } }, []);
assert.equal(text('sysEpeverBig'), '—');
assert.equal(text('sysEpeverS1'), 'Old reading');

const on = inverters([10, 0, 3]).concat([plug({ power_w: 120, current_a: 0.52 })]);
draw(null, on);
assert.equal(state('sysCharger'), 'lit');
assert.equal(text('sysChargerBig'), '120 W');
assert.equal(text('sysChargerS1'), 'On');
assert.ok(text('sysChargerS2').includes('230 V'));
assert.ok(pair('sysLCharger')[0].classList.contains('on'));
assert.ok(pair('sysFCharger')[0].classList.contains('on'));
assert.ok(pair('sysLWCharger')[0].classList.contains('on'));
assert.ok(pair('sysFWCharger')[0].classList.contains('on'));
assert.equal(text('sysOutputBig'), '13 W');
both('sysChargerBig');
both('sysOutputBig');
// Inverter plug graphics share the feed's .on class (Inv1000=10, GarInv=0, Inv600=3).
assert.ok(pair('sysPlug1000')[0].classList.contains('on'));
assert.ok(pair('sysPlugW1000')[0].classList.contains('on'));
assert.ok(pair('sysPlug600')[0].classList.contains('on'));
assert.ok(pair('sysPlugW600')[0].classList.contains('on'));
assert.ok(!pair('sysPlugGardenUp')[0].classList.contains('on'));
assert.ok(!pair('sysPlugWGardenUp')[0].classList.contains('on'));

draw(null, inverters([10, 0, 3]).concat([plug({ power_w: 0 })]));
assert.equal(state('sysCharger'), 'idle');
assert.equal(text('sysChargerBig'), '0 W');
assert.equal(text('sysOutputBig'), '13 W');
assert.ok(!pair('sysLCharger')[0].classList.contains('on'));
assert.ok(!pair('sysPlugGardenUp')[0].classList.contains('on'));

draw(null, inverters([1, 2, 3]).concat([plug({ power_w: 0.1 })]));
assert.equal(state('sysCharger'), 'lit');
assert.equal(text('sysOutputBig'), '6 W');
assert.ok(pair('sysPlug1000')[0].classList.contains('on'));
assert.ok(pair('sysPlugGardenUp')[0].classList.contains('on'));
assert.ok(pair('sysPlug600')[0].classList.contains('on'));

draw(null, [plug({ stale: true, power_w: 80 })]);
assert.equal(state('sysCharger'), 'stale');
assert.equal(text('sysChargerBig'), '—');
assert.equal(text('sysChargerS1'), 'Old reading');
assert.equal(text('sysOutputBig'), '—');
assert.ok(!pair('sysLCharger')[0].classList.contains('on'));

draw(null, [plug({ online: false, power_w: 80 })]);
assert.equal(state('sysCharger'), 'stale');
assert.equal(text('sysChargerBig'), '—');
assert.equal(text('sysChargerS1'), 'Offline');

draw(null, []);
assert.equal(state('sysCharger'), 'stale');
assert.equal(text('sysChargerBig'), '—');
assert.equal(text('sysChargerS1'), 'No reading');
assert.equal(text('sysChargerS2'), '');

console.log('PASS: Epever dark-box wording and charger plug states, with the charger left out of the solar total.');

// System detail: the nine devices in button order, and every diagram box names one of them.
const devStart = html.indexOf('const SYS_DEVICES=');
const devEnd = html.indexOf('};', devStart) + 2;
const devices = vm.runInNewContext('(' + html.slice(devStart + 'const SYS_DEVICES='.length, devEnd - 1) + ')');
const order = ['output', 'inv1000', 'inv600', 'garden', 'shed', 'garage', 'p275', 'epever', 'charger'];
assert.deepEqual(Object.keys(devices), order);
const buttons = [...html.split('id="sysDetailTabs"')[1].split('</div>')[0].matchAll(/data-dev="(\w+)"/g)].map(m => m[1]);
assert.deepEqual(buttons, order);
const boxes = [...html.matchAll(/<g id="(sys\w+)" data-dev="(\w+)"/g)];
assert.equal(boxes.length, 30, 'fifteen boxes in each of the two layouts');
for (const [, id, dev] of boxes) {
  assert.ok(devices[dev], id + ' names an unknown device ' + dev);
  assert.ok(html.includes('id="sys' + devices[dev].box + 'Big"'), dev + ' button has no headline box');
}
for (const d of Object.values(devices)) assert.ok(['Out', 'Plug', 'Meter', 'Ep'].includes(d.view));
for (const view of ['Out', 'Plug', 'Meter', 'Ep']) assert.ok(html.includes('id="sd' + view + 'View"'), view);
assert.ok(!html.includes('data-goto'), 'no box jumps to another tab');
console.log('PASS: nine System detail devices in order, every diagram box mapped to one.');
